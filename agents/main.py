import os
import json
import re
import sys
import subprocess
import uuid
from tempfile import NamedTemporaryFile
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from google.cloud import secretmanager
from tools.mcp_server import health_check, summarize_csv, word_count

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = PROJECT_ROOT / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "system_prompt.txt"

default_prompt = (
    PROMPT_FILE.read_text(encoding="utf-8").strip()
    if PROMPT_FILE.exists()
    else "You are a quantitative data analysis expert. Answer clearly and concisely."
)


def _read_secret(secret_id: str, project_id: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8").strip()


def _infer_gcp_project_id() -> str:
    return (
        os.getenv("VERTEX_PROJECT_ID", "").strip()
        or os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        or os.getenv("GCP_PROJECT", "").strip()
        or os.getenv("GCLOUD_PROJECT", "").strip()
    )

API_KEY = os.getenv("VERTEX_API_KEY", "")
AUTH_MODE = os.getenv("VERTEX_AUTH_MODE", "iam").strip().lower()
PROJECT_ID = _infer_gcp_project_id()
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1").strip()
MODEL_NAME = os.getenv("VERTEX_MODEL_NAME", "gemini-2.5-flash")
GSM_PROJECT_ID = os.getenv("GSM_PROJECT_ID", "").strip() or PROJECT_ID
GSM_VERTEX_API_KEY_SECRET = os.getenv("GSM_VERTEX_API_KEY_SECRET", "").strip()
GSM_SYSTEM_INSTRUCTION_SECRET = os.getenv("GSM_SYSTEM_INSTRUCTION_SECRET", "").strip()
ANALYSIS_MAX_RETRIES = int(os.getenv("ANALYSIS_MAX_RETRIES", "3"))
ANALYSIS_TIMEOUT_SECONDS = int(os.getenv("ANALYSIS_TIMEOUT_SECONDS", "45"))
SYSTEM_INSTRUCTION = os.getenv(
    "VERTEX_SYSTEM_INSTRUCTION",
    default_prompt,
)

STARTUP_CONFIG_ERROR = ""

if GSM_SYSTEM_INSTRUCTION_SECRET:
    if not GSM_PROJECT_ID:
        STARTUP_CONFIG_ERROR = "GSM_SYSTEM_INSTRUCTION_SECRET is set but GSM_PROJECT_ID / VERTEX_PROJECT_ID is missing"
    else:
        try:
            SYSTEM_INSTRUCTION = _read_secret(GSM_SYSTEM_INSTRUCTION_SECRET, GSM_PROJECT_ID)
        except Exception as exc:
            STARTUP_CONFIG_ERROR = f"Failed to load system instruction from Secret Manager: {exc}"

if AUTH_MODE == "api_key" and not API_KEY and GSM_VERTEX_API_KEY_SECRET:
    if not GSM_PROJECT_ID:
        STARTUP_CONFIG_ERROR = STARTUP_CONFIG_ERROR or "GSM_VERTEX_API_KEY_SECRET is set but GSM_PROJECT_ID / VERTEX_PROJECT_ID is missing"
    else:
        try:
            API_KEY = _read_secret(GSM_VERTEX_API_KEY_SECRET, GSM_PROJECT_ID)
        except Exception as exc:
            STARTUP_CONFIG_ERROR = STARTUP_CONFIG_ERROR or f"Failed to load API key from Secret Manager: {exc}"

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

if AUTH_MODE not in {"iam", "api_key"}:
    STARTUP_CONFIG_ERROR = STARTUP_CONFIG_ERROR or "VERTEX_AUTH_MODE must be either 'iam' or 'api_key'"

if AUTH_MODE == "api_key" and not API_KEY:
    STARTUP_CONFIG_ERROR = STARTUP_CONFIG_ERROR or "Missing VERTEX_API_KEY when VERTEX_AUTH_MODE=api_key"

if AUTH_MODE == "iam" and not PROJECT_ID:
    STARTUP_CONFIG_ERROR = STARTUP_CONFIG_ERROR or "Missing VERTEX_PROJECT_ID/GOOGLE_CLOUD_PROJECT when VERTEX_AUTH_MODE=iam"

# 1. Initialize FastAPI app
app = FastAPI()

# 2. Important: configure CORS so the React frontend can call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Initialize Gemini client (IAM for production, API key for local/dev fallback)
client = None
if not STARTUP_CONFIG_ERROR:
    try:
        if AUTH_MODE == "iam":
            client = genai.Client(vertexai=True, project=PROJECT_ID, location=VERTEX_LOCATION)
        else:
            client = genai.Client(api_key=API_KEY)
    except Exception as exc:
        STARTUP_CONFIG_ERROR = f"Failed to initialize Gemini client: {exc}"

# 4. Define request payload model from frontend
class ChatRequest(BaseModel):
    message: str
    csv_file_path: str | None = None


TOOL_REGISTRY = {
    "health_check": health_check,
    "summarize_csv": summarize_csv,
    "word_count": word_count,
}


@app.get("/")
async def root_status():
    return {
        "status": "ok",
        "service": "quantitative-forge-backend",
        "message": "Service is running. Use POST /api/chat or POST /api/upload-csv.",
    }


@app.get("/healthz")
async def healthz():
    return {
        "status": "healthy",
        "auth_mode": AUTH_MODE,
        "project_id": PROJECT_ID,
        "model": MODEL_NAME,
        "configured": not bool(STARTUP_CONFIG_ERROR),
    }


ANALYSIS_KEYWORDS = (
    "analy",
    "insight",
    "anomal",
    "trend",
    "summary",
    "summarize",
    "fraud",
    "outlier",
    "forecast",
    "metric",
    "分析",
    "异常",
    "趋势",
    "总结",
    "洞察",
    "欺诈",
)

DISALLOWED_CODE_PATTERNS = (
    "import os",
    "from os",
    "import subprocess",
    "from subprocess",
    "import socket",
    "from socket",
    "import requests",
    "from requests",
    "import urllib",
    "from urllib",
    "import httpx",
    "from httpx",
    "import shutil",
    "from shutil",
    "eval(",
    "exec(",
    "__import__(",
    "open(",
)


def _try_execute_tool_command(message: str):
    """Execute tools when message uses: tool:<name> {json_args}"""
    raw = message.strip()
    if not raw.startswith("tool:"):
        return None

    command = raw[len("tool:") :].strip()
    if not command:
        return {
            "error": "Invalid tool command. Use: tool:<name> {json_args}",
        }

    if " " in command:
        tool_name, arg_blob = command.split(" ", 1)
        arg_blob = arg_blob.strip()
    else:
        tool_name, arg_blob = command, ""

    func = TOOL_REGISTRY.get(tool_name)
    if not func:
        return {
            "error": f"Unknown tool: {tool_name}",
            "available_tools": sorted(TOOL_REGISTRY.keys()),
        }

    kwargs = {}
    if arg_blob:
        try:
            parsed = json.loads(arg_blob)
        except json.JSONDecodeError as exc:
            return {
                "error": "Tool arguments must be valid JSON object.",
                "details": str(exc),
            }
        if not isinstance(parsed, dict):
            return {"error": "Tool arguments must be a JSON object."}
        kwargs = parsed

    try:
        return func(**kwargs)
    except TypeError as exc:
        return {
            "error": "Invalid tool arguments.",
            "details": str(exc),
        }


def _strip_code_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\\n", "", cleaned)
        cleaned = re.sub(r"\\n```$", "", cleaned)
    return cleaned.strip()


def _extract_csv_path(message: str) -> str | None:
    patterns = [
        r'"([^"\\n]+\\.csv)"',
        r"'([^'\\n]+\\.csv)'",
        r"([^\\s,;]+\\.csv)",
    ]

    candidates = []
    for pattern in patterns:
        candidates.extend(re.findall(pattern, message, flags=re.IGNORECASE))

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)

        path = Path(candidate)
        resolved = path if path.is_absolute() else PROJECT_ROOT / path
        if resolved.exists() and resolved.is_file():
            return str(resolved.resolve())

    if candidates:
        first = Path(candidates[0])
        resolved = first if first.is_absolute() else PROJECT_ROOT / first
        return str(resolved)
    return None


def _resolve_csv_path(csv_file_path: str | None) -> str | None:
    if not csv_file_path:
        return None

    raw_path = Path(csv_file_path)
    candidate = raw_path if raw_path.is_absolute() else PROJECT_ROOT / raw_path
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None

    if resolved.suffix.lower() != ".csv":
        return None

    if not str(resolved).startswith(str(PROJECT_ROOT.resolve())):
        return None

    return str(resolved)


def _should_run_quant_analysis(message: str, csv_file_path: str | None = None) -> bool:
    if csv_file_path:
        # If frontend already uploaded a CSV, always route to quant analysis.
        return True

    lower = message.lower()
    has_csv = ".csv" in lower or bool(csv_file_path)
    has_analysis_intent = any(token in lower for token in ANALYSIS_KEYWORDS)
    return has_csv and has_analysis_intent


def _generate_python_analysis_code(user_message: str, csv_file_path: str, previous_error: str | None = None, previous_code: str | None = None) -> str:
    if client is None:
        raise RuntimeError(STARTUP_CONFIG_ERROR or "Gemini client is not initialized")

    repair_context = ""
    if previous_error:
        repair_context = (
            "Previous attempt failed. Fix the code and keep the same output contract.\\n"
            f"Error log:\\n{previous_error}\\n"
        )
    if previous_code:
        repair_context += f"Previous code:\\n{previous_code}\\n"

    generation_prompt = f"""
You are writing executable Python code for quantitative data analysis.
Generate Python code only. Do not include markdown fences.

User objective:
{user_message}

Target CSV file path (must use exactly this path):
{csv_file_path}

Requirements:
- Use pandas and numpy.
- Read the file with pandas read_csv.
- Handle common issues: missing values, duplicate rows, mixed data types.
- Compute relevant metrics and anomaly/trend signals aligned with user intent.
- Print exactly one JSON object to stdout with this schema:
  {{
    "execution_logic": "short explanation of processing steps",
    "data_insights": ["insight 1", "insight 2"],
    "metrics": {{"key": value}}
  }}
- Keep output values derived only from computed data.
- Do not use network, shell commands, or file writes.

{repair_context}
""".strip()

    response = client.models.generate_content(model=MODEL_NAME, contents=generation_prompt)
    code = _strip_code_fences(response.text or "")
    if not code:
        raise ValueError("Generated analysis code was empty.")

    lowered = code.lower()
    for pattern in DISALLOWED_CODE_PATTERNS:
        if pattern in lowered:
            raise ValueError(f"Generated code contains disallowed pattern: {pattern}")

    return code


def _execute_analysis_code(code: str) -> tuple[dict | None, str | None]:
    tmp_file_path = None
    try:
        with NamedTemporaryFile(mode="w", suffix="_analysis.py", delete=False, dir=str(PROJECT_ROOT), encoding="utf-8") as tmp_file:
            tmp_file.write(code)
            tmp_file_path = tmp_file.name

        completed = subprocess.run(
            [sys.executable, tmp_file_path],
            capture_output=True,
            text=True,
            timeout=ANALYSIS_TIMEOUT_SECONDS,
            cwd=str(PROJECT_ROOT),
        )

        if completed.returncode != 0:
            error_output = (completed.stderr or completed.stdout or "Unknown execution failure").strip()
            return None, error_output

        stdout = (completed.stdout or "").strip()
        if not stdout:
            return None, "Execution succeeded but produced no stdout JSON output."

        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict):
                return parsed, None
        except json.JSONDecodeError:
            pass

        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    return parsed, None
            except json.JSONDecodeError:
                continue

        return None, f"Could not parse JSON from stdout: {stdout[:2000]}"
    except subprocess.TimeoutExpired:
        return None, f"Execution timed out after {ANALYSIS_TIMEOUT_SECONDS} seconds."
    finally:
        if tmp_file_path:
            try:
                Path(tmp_file_path).unlink(missing_ok=True)
            except OSError:
                pass


def _format_quant_reply(result: dict) -> str:
    execution_logic = result.get("execution_logic")
    insights = result.get("data_insights")
    metrics = result.get("metrics")

    if not isinstance(execution_logic, str) or not execution_logic.strip():
        execution_logic = "Read the CSV, profiled data quality, and computed quantitative summaries relevant to the request."

    insight_lines = []
    if isinstance(insights, list):
        insight_lines = [str(item).strip() for item in insights if str(item).strip()]
    elif isinstance(insights, str) and insights.strip():
        insight_lines = [insights.strip()]

    if isinstance(metrics, dict) and metrics:
        metrics_preview = json.dumps(metrics, ensure_ascii=False)
        insight_lines.append(f"Computed metrics: {metrics_preview}")

    if not insight_lines:
        insight_lines = ["No significant pattern was detected from the available data."]

    insights_block = "\n".join(f"- {line}" for line in insight_lines)
    return f"Execution Logic\n{execution_logic}\n\nData Insights\n{insights_block}"


def _run_quant_analysis(message: str, csv_file_path: str | None = None) -> tuple[str | None, str | None]:
    csv_path = _resolve_csv_path(csv_file_path) or _extract_csv_path(message)
    if not csv_path:
        return None, None

    if not Path(csv_path).exists():
        return (
            None,
            f"I could not find the CSV file at: {csv_path}. Please provide a valid path inside the project directory.",
        )

    analysis_message = (message or "").strip()
    lower = analysis_message.lower()
    has_intent = any(token in lower for token in ANALYSIS_KEYWORDS)
    if not has_intent:
        analysis_message = (
            f"{analysis_message}\n"
            "Perform a general quantitative report: data quality checks, key metrics, trend summary, and anomaly scan."
        ).strip()

    last_error = None
    previous_code = None
    for _ in range(max(1, ANALYSIS_MAX_RETRIES)):
        try:
            code = _generate_python_analysis_code(
                user_message=analysis_message,
                csv_file_path=csv_path,
                previous_error=last_error,
                previous_code=previous_code,
            )
        except Exception as exc:
            return None, f"Failed to generate safe analysis code: {exc}"

        result, error = _execute_analysis_code(code)
        if result is not None:
            return _format_quant_reply(result), None

        last_error = error
        previous_code = code

    return None, f"Analysis execution failed after {ANALYSIS_MAX_RETRIES} attempts. Last error: {last_error}"


@app.post("/api/upload-csv")
async def upload_csv(file: UploadFile = File(...)):
    try:
        filename = file.filename or "uploaded.csv"
        safe_name = Path(filename).name
        if not safe_name.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail="Only .csv files are supported.")

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="CSV file is too large. Max size is 20MB.")

        stored_name = f"{uuid.uuid4().hex}_{safe_name}"
        stored_path = UPLOAD_DIR / stored_name
        with stored_path.open("wb") as f:
            f.write(content)

        relative_path = stored_path.relative_to(PROJECT_ROOT).as_posix()
        return {
            "status": "success",
            "file_name": safe_name,
            "csv_file_path": relative_path,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

# 5. Define backend API route
@app.post("/api/chat")
async def chat_with_ai(request: ChatRequest):
    try:
        if STARTUP_CONFIG_ERROR or client is None:
            raise HTTPException(status_code=500, detail=f"Server configuration error: {STARTUP_CONFIG_ERROR or 'Gemini client unavailable'}")

        tool_result = _try_execute_tool_command(request.message)
        if tool_result is not None:
            return {
                "reply": json.dumps(tool_result, ensure_ascii=False, indent=2),
                "status": "success",
                "source": "tool",
            }

        if _should_run_quant_analysis(request.message, request.csv_file_path):
            analysis_reply, analysis_error = _run_quant_analysis(request.message, request.csv_file_path)
            if analysis_reply is not None:
                return {
                    "reply": analysis_reply,
                    "status": "success",
                    "source": "quant-engine",
                }
            if analysis_error is not None:
                return {
                    "reply": analysis_error,
                    "status": "error",
                    "source": "quant-engine",
                }

        # Send frontend message to the model with system instruction context
        prompt = f"System instruction:\n{SYSTEM_INSTRUCTION}\n\nUser message:\n{request.message}"
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        
        # Return AI response to frontend
        return {
            "reply": response.text or "",
            "status": "success",
            "source": "model",
        }
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))