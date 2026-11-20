import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ASSISTANT_DIR = Path(__file__).resolve().parent
load_dotenv(ASSISTANT_DIR.parents[1] / ".env")

PROMPT_MAESTRO = (ASSISTANT_DIR / "PROMPT.md").read_text(encoding="utf-8").strip()
MODEL = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")

client = OpenAI(
    base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    api_key=os.getenv("NVIDIA_API_KEY"),
    timeout=20,
    max_retries=1,
)


def responder(mensaje: str, contexto: str = "") -> str:
    system = f"{PROMPT_MAESTRO}\n\n[CONTEXTO]\n{contexto or 'Sin datos registrados.'}"
    completion = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"<mensaje_usuario>\n{mensaje}\n</mensaje_usuario>"},
        ],
        temperature=0.3,
        top_p=0.95,
        max_tokens=1024,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return completion.choices[0].message.content.strip()
