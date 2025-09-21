print("Proxy starting...")
print("GitHub: https://github.com/Mirrowel/LLM-API-Key-Proxy")
import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from dotenv import load_dotenv
import logging
import colorlog
from pathlib import Path
import sys
import json
from typing import AsyncGenerator, Any, List, Optional, Union
from pydantic import BaseModel
import argparse
import litellm


# --- Pydantic Models ---
class EmbeddingRequest(BaseModel):
    model: str
    input: Union[str, List[str]]
    input_type: Optional[str] = None
    dimensions: Optional[int] = None
    user: Optional[str] = None

# --- Argument Parsing ---
parser = argparse.ArgumentParser(description="API Key Proxy Server")
parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind the server to.")
parser.add_argument("--port", type=int, default=8000, help="Port to run the server on.")
parser.add_argument("--enable-request-logging", action="store_true", help="Enable request logging.")
args, _ = parser.parse_known_args()


# Add the 'src' directory to the Python path to allow importing 'rotating_api_key_client'
sys.path.append(str(Path(__file__).resolve().parent.parent))

from rotator_library import RotatingClient, PROVIDER_PLUGINS
from proxy_app.request_logger import log_request_response, log_request_to_console
from proxy_app.batch_manager import EmbeddingBatcher

# --- Logging Configuration ---
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Configure a file handler for INFO-level logs and higher
info_file_handler = logging.FileHandler(LOG_DIR / "proxy.log", encoding="utf-8")
info_file_handler.setLevel(logging.INFO)
info_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

# Configure a dedicated file handler for all DEBUG-level logs
debug_file_handler = logging.FileHandler(LOG_DIR / "proxy_debug.log", encoding="utf-8")
debug_file_handler.setLevel(logging.DEBUG)
debug_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

# Create a filter to ensure the debug handler ONLY gets DEBUG messages from the rotator_library
class RotatorDebugFilter(logging.Filter):
    def filter(self, record):
        return record.levelno == logging.DEBUG and record.name.startswith('rotator_library')
debug_file_handler.addFilter(RotatorDebugFilter())

# Configure a console handler with color
console_handler = colorlog.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
formatter = colorlog.ColoredFormatter(
    '%(log_color)s%(message)s',
    log_colors={
        'DEBUG':    'cyan',
        'INFO':     'green',
        'WARNING':  'yellow',
        'ERROR':    'red',
        'CRITICAL': 'red,bg_white',
    }
)
console_handler.setFormatter(formatter)

# Add a filter to prevent any LiteLLM logs from cluttering the console
class NoLiteLLMLogFilter(logging.Filter):
    def filter(self, record):
        return not record.name.startswith('LiteLLM')
console_handler.addFilter(NoLiteLLMLogFilter())

# Get the root logger and set it to DEBUG to capture all messages
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# Add all handlers to the root logger
root_logger.addHandler(info_file_handler)
root_logger.addHandler(console_handler)
root_logger.addHandler(debug_file_handler)

# Silence other noisy loggers by setting their level higher than root
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Isolate LiteLLM's logger to prevent it from reaching the console.
# We will capture its logs via the logger_fn callback in the client instead.
litellm_logger = logging.getLogger("LiteLLM")
litellm_logger.handlers = []
litellm_logger.propagate = False
# Load environment variables from .env file
load_dotenv()

# --- Configuration ---
USE_EMBEDDING_BATCHER = False
ENABLE_REQUEST_LOGGING = args.enable_request_logging
PROXY_API_KEY = os.getenv("PROXY_API_KEY")
if not PROXY_API_KEY:
    raise ValueError("PROXY_API_KEY environment variable not set.")

# Load all provider API keys from environment variables
api_keys = {}
for key, value in os.environ.items():
    # Exclude PROXY_API_KEY from being treated as a provider API key
    if (key.endswith("_API_KEY") or "_API_KEY_" in key) and key != "PROXY_API_KEY":
        parts = key.split("_API_KEY")
        provider = parts[0].lower()
        if provider not in api_keys:
            api_keys[provider] = []
        api_keys[provider].append(value)

if not api_keys:
    raise ValueError("No provider API keys found in environment variables.")

# --- Lifespan Management ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the RotatingClient's lifecycle with the app's lifespan."""
     # The client now uses the root logger configuration
    client = RotatingClient(api_keys=api_keys, configure_logging=True)
    client.start_model_cache_population()  # Start the background task
    app.state.rotating_client = client
    os.environ["LITELLM_LOG"] = "ERROR"
    litellm.set_verbose = False
    litellm.drop_params = True
    if USE_EMBEDDING_BATCHER:
        batcher = EmbeddingBatcher(client=client)
        app.state.embedding_batcher = batcher
        logging.info("RotatingClient and EmbeddingBatcher initialized.")
    else:
        app.state.embedding_batcher = None
        logging.info("RotatingClient initialized (EmbeddingBatcher disabled).")
        
    yield
    
    if app.state.embedding_batcher:
        await app.state.embedding_batcher.stop()
    await client.close()
    
    if app.state.embedding_batcher:
        logging.info("RotatingClient and EmbeddingBatcher closed.")
    else:
        logging.info("RotatingClient closed.")

# --- FastAPI App Setup ---
app = FastAPI(lifespan=lifespan)

# Add CORS middleware to allow all origins, methods, and headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)

def get_rotating_client(request: Request) -> RotatingClient:
    """Dependency to get the rotating client instance from the app state."""
    return request.app.state.rotating_client

def get_embedding_batcher(request: Request) -> EmbeddingBatcher:
    """Dependency to get the embedding batcher instance from the app state."""
    return request.app.state.embedding_batcher

async def verify_api_key(auth: str = Depends(api_key_header)):
    """Dependency to verify the proxy API key."""
    if not auth or auth != f"Bearer {PROXY_API_KEY}":
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return auth

async def streaming_response_wrapper(
    request: Request,
    request_data: dict,
    response_stream: AsyncGenerator[str, None]
) -> AsyncGenerator[str, None]:
    """
    Wraps a streaming response to log the full response after completion
    and ensures any errors during the stream are sent to the client.
    """
    response_chunks = []
    full_response = {}
    
    try:
        async for chunk_str in response_stream:
            if await request.is_disconnected():
                logging.warning("Client disconnected, stopping stream.")
                break
            yield chunk_str
            if chunk_str.strip() and chunk_str.startswith("data:"):
                content = chunk_str[len("data:"):].strip()
                if content != "[DONE]":
                    try:
                        chunk_data = json.loads(content)
                        response_chunks.append(chunk_data)
                    except json.JSONDecodeError:
                        pass  # Ignore non-JSON chunks
    except Exception as e:
        logging.error(f"An error occurred during the response stream: {e}")
        # Yield a final error message to the client to ensure they are not left hanging.
        error_payload = {
            "error": {
                "message": f"An unexpected error occurred during the stream: {str(e)}",
                "type": "proxy_internal_error",
                "code": 500
            }
        }
        yield f"data: {json.dumps(error_payload)}\n\n"
        yield "data: [DONE]\n\n"
        # Also log this as a failed request
        if ENABLE_REQUEST_LOGGING:
            log_request_response(
                request_data=request_data,
                response_data={"error": str(e)},
                is_streaming=True,
                log_type="completion"
            )
        return # Stop further processing
    finally:
        if response_chunks:
            # --- Aggregation Logic ---
            final_message = {"role": "assistant"}
            aggregated_tool_calls = {}
            usage_data = None
            finish_reason = None

            for chunk in response_chunks:
                if "choices" in chunk and chunk["choices"]:
                    choice = chunk["choices"][0]
                    delta = choice.get("delta", {})

                    # Dynamically aggregate all fields from the delta
                    for key, value in delta.items():
                        if value is None:
                            continue

                        if key == "content":
                            if "content" not in final_message:
                                final_message["content"] = ""
                            if value:
                                final_message["content"] += value
                        
                        elif key == "tool_calls":
                            for tc_chunk in value:
                                index = tc_chunk["index"]
                                if index not in aggregated_tool_calls:
                                    aggregated_tool_calls[index] = {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}
                                if tc_chunk.get("id"):
                                    aggregated_tool_calls[index]["id"] = tc_chunk["id"]
                                if "function" in tc_chunk:
                                    if "name" in tc_chunk["function"]:
                                        aggregated_tool_calls[index]["function"]["name"] += tc_chunk["function"]["name"]
                                    if "arguments" in tc_chunk["function"]:
                                        aggregated_tool_calls[index]["function"]["arguments"] += tc_chunk["function"]["arguments"]
                        
                        elif key == "function_call":
                            if "function_call" not in final_message:
                                final_message["function_call"] = {"name": "", "arguments": ""}
                            if "name" in value:
                                final_message["function_call"]["name"] += value["name"]
                            if "arguments" in value:
                                final_message["function_call"]["arguments"] += value["arguments"]
                        
                        else: # Generic key handling for other data like 'reasoning'
                            if key not in final_message:
                                final_message[key] = value
                            elif isinstance(final_message.get(key), str):
                                final_message[key] += value
                            else:
                                final_message[key] = value

                    if "finish_reason" in choice and choice["finish_reason"]:
                        finish_reason = choice["finish_reason"]

                if "usage" in chunk and chunk["usage"]:
                    usage_data = chunk["usage"]

            # --- Final Response Construction ---
            if aggregated_tool_calls:
                final_message["tool_calls"] = list(aggregated_tool_calls.values())

            # Ensure standard fields are present for consistent logging
            for field in ["content", "tool_calls", "function_call"]:
                if field not in final_message:
                    final_message[field] = None

            first_chunk = response_chunks[0]
            final_choice = {
                "index": 0,
                "message": final_message,
                "finish_reason": finish_reason
            }

            full_response = {
                "id": first_chunk.get("id"),
                "object": "chat.completion",
                "created": first_chunk.get("created"),
                "model": first_chunk.get("model"),
                "choices": [final_choice],
                "usage": usage_data
            }

        if ENABLE_REQUEST_LOGGING:
            log_request_response(
                request_data=request_data,
                response_data=full_response,
                is_streaming=True,
                log_type="completion"
            )

@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    client: RotatingClient = Depends(get_rotating_client),
    _ = Depends(verify_api_key)
):
    """
    OpenAI-compatible endpoint powered by the RotatingClient.
    Handles both streaming and non-streaming responses and logs them.
    """
    try:
        request_data = await request.json()
        log_request_to_console(
            url=str(request.url),
            headers=dict(request.headers),
            client_info=(request.client.host, request.client.port),
            request_data=request_data
        )
        is_streaming = request_data.get("stream", False)

        if is_streaming:
            response_generator = client.acompletion(request=request, **request_data)
            return StreamingResponse(
                streaming_response_wrapper(request, request_data, response_generator),
                media_type="text/event-stream"
            )
        else:
            response = await client.acompletion(request=request, **request_data)
            if ENABLE_REQUEST_LOGGING:
                log_request_response(
                    request_data=request_data,
                    response_data=response.model_dump(),
                    is_streaming=False,
                    log_type="completion"
                )
            return response

    except (litellm.InvalidRequestError, ValueError, litellm.ContextWindowExceededError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid Request: {str(e)}")
    except litellm.AuthenticationError as e:
        raise HTTPException(status_code=401, detail=f"Authentication Error: {str(e)}")
    except litellm.RateLimitError as e:
        raise HTTPException(status_code=429, detail=f"Rate Limit Exceeded: {str(e)}")
    except (litellm.ServiceUnavailableError, litellm.APIConnectionError) as e:
        raise HTTPException(status_code=503, detail=f"Service Unavailable: {str(e)}")
    except litellm.Timeout as e:
        raise HTTPException(status_code=504, detail=f"Gateway Timeout: {str(e)}")
    except (litellm.InternalServerError, litellm.OpenAIError) as e:
        raise HTTPException(status_code=502, detail=f"Bad Gateway: {str(e)}")
    except Exception as e:
        logging.error(f"Request failed after all retries: {e}")
        # Optionally log the failed request
        if ENABLE_REQUEST_LOGGING:
            try:
                request_data = await request.json()
            except json.JSONDecodeError:
                request_data = {"error": "Could not parse request body"}
            log_request_response(
                request_data=request_data,
                response_data={"error": str(e)},
                is_streaming=request_data.get("stream", False),
                log_type="completion"
            )
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/embeddings")
async def embeddings(
    request: Request,
    body: EmbeddingRequest,
    client: RotatingClient = Depends(get_rotating_client),
    batcher: Optional[EmbeddingBatcher] = Depends(get_embedding_batcher),
    _ = Depends(verify_api_key)
):
    """
    OpenAI-compatible endpoint for creating embeddings.
    Supports two modes based on the USE_EMBEDDING_BATCHER flag:
    - True: Uses a server-side batcher for high throughput.
    - False: Passes requests directly to the provider.
    """
    try:
        request_data = body.model_dump(exclude_none=True)
        log_request_to_console(
            url=str(request.url),
            headers=dict(request.headers),
            client_info=(request.client.host, request.client.port),
            request_data=request_data
        )
        if USE_EMBEDDING_BATCHER and batcher:
            # --- Server-Side Batching Logic ---
            request_data = body.model_dump(exclude_none=True)
            inputs = request_data.get("input", [])
            if isinstance(inputs, str):
                inputs = [inputs]

            tasks = []
            for single_input in inputs:
                individual_request = request_data.copy()
                individual_request["input"] = single_input
                tasks.append(batcher.add_request(individual_request))
            
            results = await asyncio.gather(*tasks)

            all_data = []
            total_prompt_tokens = 0
            total_tokens = 0
            for i, result in enumerate(results):
                result["data"][0]["index"] = i
                all_data.extend(result["data"])
                total_prompt_tokens += result["usage"]["prompt_tokens"]
                total_tokens += result["usage"]["total_tokens"]

            final_response_data = {
                "object": "list",
                "model": results[0]["model"],
                "data": all_data,
                "usage": { "prompt_tokens": total_prompt_tokens, "total_tokens": total_tokens },
            }
            response = litellm.EmbeddingResponse(**final_response_data)
        
        else:
            # --- Direct Pass-Through Logic ---
            request_data = body.model_dump(exclude_none=True)
            if isinstance(request_data.get("input"), str):
                request_data["input"] = [request_data["input"]]
            
            response = await client.aembedding(request=request, **request_data)

        if ENABLE_REQUEST_LOGGING:
            response_summary = {
                "model": response.model,
                "object": response.object,
                "usage": response.usage.model_dump(),
                "data_count": len(response.data),
                "embedding_dimensions": len(response.data[0].embedding) if response.data else 0
            }
            log_request_response(
                request_data=body.model_dump(exclude_none=True),
                response_data=response_summary,
                is_streaming=False,
                log_type="embedding"
            )
        return response

    except HTTPException as e:
        # Re-raise HTTPException to ensure it's not caught by the generic Exception handler
        raise e
    except (litellm.InvalidRequestError, ValueError, litellm.ContextWindowExceededError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid Request: {str(e)}")
    except litellm.AuthenticationError as e:
        raise HTTPException(status_code=401, detail=f"Authentication Error: {str(e)}")
    except litellm.RateLimitError as e:
        raise HTTPException(status_code=429, detail=f"Rate Limit Exceeded: {str(e)}")
    except (litellm.ServiceUnavailableError, litellm.APIConnectionError) as e:
        raise HTTPException(status_code=503, detail=f"Service Unavailable: {str(e)}")
    except litellm.Timeout as e:
        raise HTTPException(status_code=504, detail=f"Gateway Timeout: {str(e)}")
    except (litellm.InternalServerError, litellm.OpenAIError) as e:
        raise HTTPException(status_code=502, detail=f"Bad Gateway: {str(e)}")
    except Exception as e:
        logging.error(f"Embedding request failed: {e}")
        if ENABLE_REQUEST_LOGGING:
            try:
                request_data = await request.json()
            except json.JSONDecodeError:
                request_data = {"error": "Could not parse request body"}
            log_request_response(
                request_data=request_data,
                response_data={"error": str(e)},
                is_streaming=False,
                log_type="embedding"
            )
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def read_root():
    return {"Status": "API Key Proxy is running"}

@app.get("/v1/models")
async def list_models(
    grouped: bool = False, 
    client: RotatingClient = Depends(get_rotating_client),
    _=Depends(verify_api_key)
):
    """
    Returns a list of available models from all configured providers.
    Waits for the background cache to be ready before returning.
    """
    models = await client.get_all_available_models(grouped=grouped)
    return models

@app.get("/v1/providers")
async def list_providers(_=Depends(verify_api_key)):
    """
    Returns a list of all available providers.
    """
    return list(PROVIDER_PLUGINS.keys())

@app.post("/v1/token-count")
async def token_count(
    request: Request, 
    client: RotatingClient = Depends(get_rotating_client),
    _=Depends(verify_api_key)
):
    """
    Calculates the token count for a given list of messages and a model.
    """
    try:
        data = await request.json()
        model = data.get("model")
        messages = data.get("messages")

        if not model or not messages:
            raise HTTPException(status_code=400, detail="'model' and 'messages' are required.")

        count = client.token_count(**data)
        return {"token_count": count}

    except Exception as e:
        logging.error(f"Token count failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
