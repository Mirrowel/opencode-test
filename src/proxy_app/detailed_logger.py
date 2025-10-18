import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List
import logging

LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
DETAILED_LOGS_DIR = LOGS_DIR / "detailed_logs"

class DetailedLogger:
    """
    Logs comprehensive details of each API transaction to a unique, timestamped directory.
    """
    def __init__(self):
        """
        Initializes the logger for a single request, creating a unique directory to store all related log files.
        """
        self.start_time = time.time()
        self.request_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_dir = DETAILED_LOGS_DIR / f"{timestamp}_{self.request_id}"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.streaming = False

    def _write_json(self, filename: str, data: Dict[str, Any]):
        """Helper to write data to a JSON file in the log directory."""
        try:
            with open(self.log_dir / filename, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logging.error(f"[{self.request_id}] Failed to write to {filename}: {e}")

    def log_request(self, headers: Dict[str, Any], body: Dict[str, Any]):
        """Logs the initial request details."""
        self.streaming = body.get("stream", False)
        request_data = {
            "request_id": self.request_id,
            "timestamp_utc": datetime.utcnow().isoformat(),
            "headers": dict(headers),
            "body": body
        }
        self._write_json("request.json", request_data)

    def log_stream_chunk(self, chunk: Dict[str, Any]):
        """Logs an individual chunk from a streaming response to a JSON Lines file."""
        try:
            log_entry = {
                "timestamp_utc": datetime.utcnow().isoformat(),
                "chunk": chunk
            }
            with open(self.log_dir / "streaming_chunks.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logging.error(f"[{self.request_id}] Failed to write stream chunk: {e}")

    def log_final_response(self, status_code: int, headers: Optional[Dict[str, Any]], body: Dict[str, Any]):
        """Logs the complete final response, either from a non-streaming call or after reassembling a stream."""
        end_time = time.time()
        duration_ms = (end_time - self.start_time) * 1000

        response_data = {
            "request_id": self.request_id,
            "timestamp_utc": datetime.utcnow().isoformat(),
            "status_code": status_code,
            "duration_ms": round(duration_ms),
            "headers": dict(headers) if headers else None,
            "body": body
        }
        self._write_json("final_response.json", response_data)
        self._log_metadata(response_data)

    def _extract_reasoning(self, response_body: Dict[str, Any]) -> Optional[str]:
        """Recursively searches for and extracts 'reasoning' fields from the response body."""
        if not isinstance(response_body, dict):
            return None
        
        if "reasoning" in response_body:
            return response_body["reasoning"]
            
        if "choices" in response_body and response_body["choices"]:
            message = response_body["choices"][0].get("message", {})
            if "reasoning" in message:
                return message["reasoning"]
            if "reasoning_content" in message:
                return message["reasoning_content"]

        return None

    def _log_metadata(self, response_data: Dict[str, Any]):
        """Logs a summary of the transaction for quick analysis."""
        usage = response_data.get("body", {}).get("usage") or {}
        model = response_data.get("body", {}).get("model", "N/A")
        finish_reason = "N/A"
        if "choices" in response_data.get("body", {}) and response_data["body"]["choices"]:
            finish_reason = response_data["body"]["choices"][0].get("finish_reason", "N/A")

        metadata = {
            "request_id": self.request_id,
            "timestamp_utc": response_data["timestamp_utc"],
            "duration_ms": response_data["duration_ms"],
            "status_code": response_data["status_code"],
            "model": model,
            "streaming": self.streaming,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            },
            "finish_reason": finish_reason,
            "reasoning_found": False,
            "reasoning_content": None
        }

        reasoning = self._extract_reasoning(response_data.get("body", {}))
        if reasoning:
            metadata["reasoning_found"] = True
            metadata["reasoning_content"] = reasoning
        
        self._write_json("metadata.json", metadata)