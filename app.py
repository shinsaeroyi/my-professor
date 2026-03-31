from flask import Flask, request, jsonify, render_template
from pathlib import Path
import json
import uuid
import time
import threading
import webbrowser

app = Flask(__name__)

DATA_DIR = Path("data/professors")
UPLOAD_DIR = Path("static/uploads")
SETTINGS_FILE = Path("data/settings.json")

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def load_settings():
    if SETTINGS_FILE.exists():
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        settings.setdefault("api_key", "")
        settings.setdefault("model", "gemini-2.5-flash")
        return settings
    return {
        "api_key": "",
        "model": "gemini-2.5-flash"
    }


def save_settings(settings):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(load_settings())


@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.json
    settings = load_settings()
    settings["api_key"] = data.get("api_key", settings.get("api_key", ""))
    settings["model"] = data.get("model", settings.get("model", "gemini-2.5-flash"))
    save_settings(settings)
    return jsonify({"success": True})

@app.route("/api/professors", methods=["GET"])
def list_professors():
    professors = []
    for f in sorted(DATA_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            professors.append({
                "id": data["id"],
                "name": data["name"],
                "affiliation": data.get("affiliation", ""),
                "photo": data.get("photo", ""),
                "speaking_style": data.get("speaking_style", ""),
                "paper_count": len(data.get("papers", [])),
            })
        except Exception:
            pass
    return jsonify(professors)


@app.route("/api/professor/<prof_id>", methods=["GET"])
def get_professor(prof_id):
    prof_file = DATA_DIR / f"{prof_id}.json"
    if not prof_file.exists():
        return jsonify({"error": "Not found"}), 404
    return jsonify(json.loads(prof_file.read_text(encoding="utf-8")))


@app.route("/api/professor/<prof_id>", methods=["DELETE"])
def delete_professor(prof_id):
    prof_file = DATA_DIR / f"{prof_id}.json"
    if prof_file.exists():
        try:
            data = json.loads(prof_file.read_text(encoding="utf-8"))
            photo = data.get("photo", "")
            if photo and photo.startswith("/static/uploads/"):
                photo_path = Path(photo.lstrip("/"))
                if photo_path.exists():
                    photo_path.unlink()
        except Exception:
            pass
        prof_file.unlink()
    return jsonify({"success": True})


def reconstruct_abstract(inverted_index):
    if not inverted_index:
        return ""
    index = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            index[pos] = word
    return " ".join(index[i] for i in sorted(index.keys()))


@app.route("/api/search-scholar-candidates", methods=["POST"])
def search_scholar_candidates():
    """이름으로 후보 저자 목록만 반환 (선택용)"""
    import requests as req_lib

    data = request.json
    name = data.get("name", "").strip()
    affil_hint = data.get("affil_hint", "").strip().lower()
    if not name:
        return jsonify({"success": False, "error": "이름을 입력해주세요."})

    try:
        author_res = req_lib.get(
            "https://api.openalex.org/authors",
            params={"search": name, "per-page": 15, "mailto": "professor.agent@lab"},
            timeout=15,
        )
        author_res.raise_for_status()
        results = author_res.json().get("results", [])

        if not results:
            return jsonify({"success": False, "error": f"'{name}' 검색 결과가 없습니다. 영문 이름으로 다시 시도해보세요."})

        candidates = []
        for a in results:
            affil = ""
            if a.get("last_known_institutions"):
                affil = a["last_known_institutions"][0].get("display_name", "")
            candidates.append({
                "id": a["id"].split("/")[-1],
                "name": a.get("display_name", ""),
                "affiliation": affil,
                "paper_count": a.get("works_count", 0),
                "topics": [t["display_name"] for t in a.get("topics", [])[:3]],
            })

        # 소속 힌트가 있으면 일치하는 항목을 맨 위로
        if affil_hint:
            def match_score(c):
                return sum(word in c["affiliation"].lower() for word in affil_hint.split())
            candidates.sort(key=match_score, reverse=True)

        return jsonify({"success": True, "candidates": candidates[:8]})

    except Exception as e:
        return jsonify({"success": False, "error": f"검색 중 오류 발생: {str(e)}"})


@app.route("/api/search-scholar", methods=["POST"])
def search_scholar():
    """선택된 저자 ID로 논문 정보를 가져옴"""
    import requests as req_lib

    data = request.json
    name = data.get("name", "").strip()
    author_id = data.get("author_id", "").strip()

    if not author_id:
        return jsonify({"success": False, "error": "저자를 선택해주세요."})

    try:
        # 1) 저자 상세 정보
        author_res = req_lib.get(
            f"https://api.openalex.org/authors/{author_id}",
            params={"mailto": "professor.agent@lab"},
            timeout=15,
        )
        author_res.raise_for_status()
        author = author_res.json()

        affiliation = ""
        if author.get("last_known_institutions"):
            affiliation = author["last_known_institutions"][0].get("display_name", "")
        interests = [t["display_name"] for t in author.get("topics", [])[:6]]

        # 2) 논문 목록 가져오기
        works_res = req_lib.get(
            "https://api.openalex.org/works",
            params={
                "filter": f"author.id:{author_id}",
                "per-page": 25,
                "sort": "cited_by_count:desc",
                "select": "title,publication_year,cited_by_count,abstract_inverted_index",
                "mailto": "professor.agent@lab",
            },
            timeout=15,
        )
        works_res.raise_for_status()
        works_data = works_res.json()

        papers = []
        for w in works_data.get("results", []):
            abstract = reconstruct_abstract(w.get("abstract_inverted_index"))
            papers.append({
                "title": w.get("title", ""),
                "year": str(w.get("publication_year", "")),
                "citations": w.get("cited_by_count", 0),
                "abstract": abstract[:300],
            })

        return jsonify({
            "success": True,
            "name": author.get("display_name", name),
            "affiliation": affiliation,
            "interests": interests,
            "papers": papers,
        })

    except Exception as e:
        return jsonify({"success": False, "error": f"검색 중 오류 발생: {str(e)}"})


@app.route("/api/upload-photo", methods=["POST"])
def upload_photo():
    if "photo" not in request.files:
        return jsonify({"error": "파일이 없습니다."}), 400
    file = request.files["photo"]
    if not file.filename:
        return jsonify({"error": "파일명이 없습니다."}), 400
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ["jpg", "jpeg", "png", "gif", "webp"]:
        return jsonify({"error": "JPG, PNG, WEBP 파일만 지원합니다."}), 400
    filename = f"{uuid.uuid4()}.{ext}"
    file.save(UPLOAD_DIR / filename)
    return jsonify({"photo_url": f"/static/uploads/{filename}"})

@app.route("/api/create-professor", methods=["POST"])
def create_professor():
    data = request.json
    prof_id = str(uuid.uuid4())[:8]
    professor = {
        "id": prof_id,
        "name": data.get("name", ""),
        "affiliation": data.get("affiliation", ""),
        "photo": data.get("photo", ""),
        "papers": data.get("papers", []),
        "interests": data.get("interests", []),
        "speaking_style": data.get("speaking_style", "팩폭형"),
        "custom_style": data.get("custom_style", ""),
        "philosophy": data.get("philosophy", ""),
        "emphasizes": data.get("emphasizes", ""),
        "dislikes": data.get("dislikes", ""),
        "famous_quotes": data.get("famous_quotes", ""),
        "feedback_examples": data.get("feedback_examples", ""),
    }
    (DATA_DIR / f"{prof_id}.json").write_text(
        json.dumps(professor, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return jsonify({"success": True, "id": prof_id})


STYLE_PROMPTS = {
    "팩폭형": (
        "당신은 직설적이고 솔직한 교수입니다. 돌려말하지 않고 문제점을 즉시 정확하게 지적합니다. "
        "'이 부분은 치명적인 문제가 있습니다', '이 접근법은 처음부터 다시 생각해야 합니다'처럼 명확하게 표현합니다. "
        "칭찬은 아끼고 개선이 필요한 부분에 집중합니다."
    ),
    "소프트킬형": (
        "당신은 겉으로는 부드럽지만 날카로운 지적을 하는 교수입니다. "
        "칭찬으로 시작하지만 결국 핵심 약점을 우아하게 파고듭니다. "
        "'흥미로운 접근이네요... 그런데 이 가정이 틀리면 전체 논지가 무너지지 않나요?'처럼 말합니다."
    ),
    "디테일장인형": (
        "당신은 세부사항에 집착하는 완벽주의 교수입니다. "
        "방법론의 모든 가정을 검토하고, 통계적 유의성, 샘플 크기, 실험 설계의 엄밀함을 끊임없이 파고듭니다. "
        "'이 p-value는 어떻게 계산했나요?', '이 조건에서 재현 가능합니까?'를 자주 묻습니다."
    ),
    "멘토형": (
        "당신은 따뜻하고 학생의 성장을 최우선으로 하는 교수입니다. "
        "비판보다는 방향 제시를 선호하고, 학생이 스스로 문제를 발견하도록 유도합니다. "
        "하지만 연구의 질에 대한 기준은 결코 타협하지 않습니다."
    ),
    "철학자형": (
        "당신은 '왜?'를 끊임없이 묻는 교수입니다. "
        "연구의 근본적인 의미와 기여를 중시하며, 방법론보다 연구 질문 자체의 가치를 먼저 검토합니다. "
        "'이 연구가 해결하려는 진짜 문제가 무엇인가요?', '이 결과가 사실이라면 세상이 어떻게 달라지나요?'를 자주 묻습니다."
    ),
}

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    prof_id = data.get("professor_id")
    messages = data.get("messages", [])
    user_message = data.get("message", "")

    settings = load_settings()
    api_key = settings.get("api_key", "")
    model_name = settings.get("model", "gemini-2.5-flash")

    if not api_key:
        return jsonify({"error": "API 키가 설정되지 않았습니다. 우측 하단 설정(⚙)에서 Gemini API 키를 입력해주세요."}), 400

    prof_file = DATA_DIR / f"{prof_id}.json"
    if not prof_file.exists():
        return jsonify({"error": "교수님 프로필을 찾을 수 없습니다."}), 404

    professor = json.loads(prof_file.read_text(encoding="utf-8"))

    papers_text = ""
    for i, paper in enumerate(professor.get("papers", [])[:20], 1):
        year = f" ({paper.get('year', '')})" if paper.get("year") else ""
        papers_text += f"{i}. {paper['title']}{year}\n"
        if paper.get("abstract"):
            papers_text += f"   → {paper['abstract'][:200]}\n"

    style_key = professor.get("speaking_style", "팩폭형")
    style_prompt = STYLE_PROMPTS.get(style_key, STYLE_PROMPTS["팩폭형"])

    system_prompt = f"""당신은 {professor['name']} 교수님입니다.
학생들과 대화할 때 교수님의 성격, 연구 철학, 말투를 완벽하게 재현하세요.

━━━ 교수님 기본 정보 ━━━
소속: {professor.get('affiliation') or '미입력'}
연구 분야: {', '.join(professor.get('interests', [])) or '미입력'}

━━━ 주요 논문 목록 (Google Scholar) ━━━
{papers_text or '논문 정보 없음'}

━━━ 말투 스타일: [{style_key}] ━━━
{style_prompt}
{f"추가 말투 특징: {professor['custom_style']}" if professor.get('custom_style') else ''}

━━━ 연구 철학 ━━━
{professor.get('philosophy') or '(미입력)'}

━━━ 교수님이 항상 강조하는 것 ━━━
{professor.get('emphasizes') or '(미입력)'}

━━━ 교수님이 싫어하는 것 ━━━
{professor.get('dislikes') or '(미입력)'}

━━━ 교수님의 명언 / 자주 하시는 말씀 ━━━
{professor.get('famous_quotes') or '(미입력)'}

━━━ 실제 피드백 예시 ━━━
{professor.get('feedback_examples') or '(미입력)'}

━━━ 절대 지켜야 할 규칙 ━━━
1. 반드시 한국어로만 대답하세요 (영어 인용 시 한국어 설명 병기)
2. 학생에게는 반드시 반말로 답하세요. 존댓말을 사용하지 마세요.
3. 위의 말투 스타일과 철학을 일관되게 유지하세요
4. 교수님의 논문과 연구 분야에 기반해서 조언하세요
5. 논문 수정 피드백 요청 시 교수님의 관점에서 구체적으로 답하세요
6. 절대 "저는 AI입니다"라고 밝히지 말고, 끝까지 교수님으로 행동하세요"""

    try:
        from google import genai

        client = genai.Client(api_key=api_key)

        history_text = ""
        for msg in messages:
            role_name = "학생" if msg["role"] == "user" else "교수님"
            history_text += f"{role_name}: {msg['content']}\n"

        full_prompt = f"""{system_prompt}

이전 대화:
{history_text}

학생: {user_message}
교수님:"""

        response = client.models.generate_content(
            model=model_name,
            contents=full_prompt,
        )

        return jsonify({"response": response.text})

    except Exception as e:
        error_msg = str(e)
        if "API_KEY_INVALID" in error_msg:
            return jsonify({"error": "API 키가 유효하지 않습니다. 설정에서 확인해주세요."}), 400
        return jsonify({"error": f"오류가 발생했습니다: {error_msg}"}), 500


if __name__ == "__main__":
    def open_browser():
        time.sleep(1.5)
        webbrowser.open("http://localhost:5000")

    threading.Thread(target=open_browser, daemon=True).start()
    print("교수님 에이전트 시작 중...")
    print("브라우저가 자동으로 열립니다...")
    app.run(debug=False, port=5000, host="127.0.0.1")
