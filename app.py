from pathlib import Path
import random, sqlite3, time
from flask import Flask, render_template, request, jsonify, url_for
import os, re, csv, random

app = Flask(__name__)
DB = "votes.db"


TITLES= {}
with open(“prompts.csv”, newline=“”, encoding=“utf-8") as f:
    reader = csv.reader(f)
    for i, row in enumerate(reader, start=1):
        TITLES[str(i)] = row[0].strip()
def prompt_number(prompt_stem: str) -> str:
    return prompt_stem.split(“_”)[-1]
def prompt_title(prompt_stem: str) -> str:
    num = prompt_number(prompt_stem)
    return TITLES.get(num, prompt_stem)
    

# --------------------------------------------------
# 1) 디렉터리 스캔:  FILES[prompt][category] = <Path>
# --------------------------------------------------
IMG_ROOT = Path("static/images")
ALLOWED_EXTS = {“.png”, “.jpg”, “.jpeg”, “.webp”, “.gif”}
FILES = {}            # { prompt : { category: Path(...) } }
for cat_dir in IMG_ROOT.iterdir():
    if not cat_dir.is_dir():
        continue
    cat = cat_dir.name
    for p in cat_dir.iterdir():
        if (
            p.is_file()
            and p.suffix.lower() in ALLOWED_EXTS
            and not p.name.startswith(“.”)
        ):
            prompt = p.stem                     # 확장자 제거
            FILES.setdefault(prompt, {})[cat] = p

PROMPTS_AVAILABLE = [k for k, v in FILES.items() if len(v) >= 2]
if not PROMPTS_AVAILABLE:
    raise RuntimeError("두 개 이상 카테고리가 가진 이미지가 없습니다!")

# --------------------------------------------------
# 2) SQLite ― WAL + Elo 테이블
# --------------------------------------------------
def get_conn():
    return sqlite3.connect(
        DB,
        check_same_thread=False,
        isolation_level=None,    # autocommit
        detect_types=sqlite3.PARSE_DECLTYPES,
    )

def init_db():
    with get_conn() as conn:
        c = conn.cursor()
        c.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS votes(
              id        INTEGER PRIMARY KEY AUTOINCREMENT,
              prompt    TEXT,
              left_cat  TEXT,
              right_cat TEXT,
              result    TEXT,   -- left / right / similar
              ts        REAL
            );
            CREATE TABLE IF NOT EXISTS elo(
              category TEXT PRIMARY KEY,
              rating   REAL
            );
        """
        )

# --------------------------------------------------
# 3) Elo 계산 (카테고리 단위)
# --------------------------------------------------
K = 32

def expected(r_a, r_b):
    return 1 / (1 + 10 ** ((r_b - r_a) / 400))

def fetch_rating(cur, cat):
    r = cur.execute("SELECT rating FROM elo WHERE category=?", (cat,)).fetchone()
    return r[0] if r else 1200.0

def update_elo(cat_l, cat_r, winner):  # winner: left/right/similar
    with get_conn() as conn:
        c = conn.cursor()
        r_l, r_r = fetch_rating(c, cat_l), fetch_rating(c, cat_r)
        e_l, e_r = expected(r_l, r_r), expected(r_r, r_l)

        if winner == "left":
            s_l, s_r = 1, 0
        elif winner == "right":
            s_l, s_r = 0, 1
        else:  # similar
            s_l = s_r = 0.5

        new_l = r_l + K * (s_l - e_l)
        new_r = r_r + K * (s_r - e_r)

        c.execute(
            "INSERT INTO elo(category,rating) VALUES(?,?) "
            "ON CONFLICT(category) DO UPDATE SET rating=excluded.rating",
            (cat_l, new_l),
        )
        c.execute(
            "INSERT INTO elo(category,rating) VALUES(?,?) "
            "ON CONFLICT(category) DO UPDATE SET rating=excluded.rating",
            (cat_r, new_r),
        )

# --------------------------------------------------
# 4) 유틸: 이미지 두 장 고르기
# --------------------------------------------------
def pick_pair():
    """prompt 하나 고르고 → 그 prompt 를 가진 서로 다른 두 카테고리 선택"""
    prompt = random.choice(PROMPTS_AVAILABLE)
    cat_left, cat_right = random.sample(list(FILES[prompt].keys()), 2)
    left_path = FILES[prompt][cat_left]
    right_path = FILES[prompt][cat_right]
    return prompt, (cat_left, left_path), (cat_right, right_path)

# --------------------------------------------------
# 5) Routes
# --------------------------------------------------
@app.route(“/”)
def index():
    prompt, (cat_l, path_l), (cat_r, path_r) = pick_pair()
    print(prompt)
    print(prompt_title(prompt))
    return render_template(
        “index.html”,
        prompt=prompt,
        prompt_text = prompt_title(prompt),
        left=url_for(“static”, filename=f”images/{cat_l}/{path_l.name}“),
        right=url_for(“static”, filename=f”images/{cat_r}/{path_r.name}“),
        left_cat=cat_l,
        right_cat=cat_r,
    )
@app.post(“/vote”)
def vote():
    data = request.json
    prompt = data[“prompt”]
    cat_l = data[“left_cat”]
    cat_r = data[“right_cat”]
    result = data[“result”]               # left / right / similar
    # 기록
    with get_conn() as conn:
        conn.execute(
            “INSERT INTO votes(prompt,left_cat,right_cat,result,ts) VALUES(?,?,?,?,?)“,
            (prompt, cat_l, cat_r, result, time.time()),
        )
    # Elo 갱신
    update_elo(cat_l, cat_r, result)
    # 새 쌍 리턴
    new_prompt, (n_cat_l, n_path_l), (n_cat_r, n_path_r) = pick_pair()
    new_title = prompt_title(new_prompt)
    return jsonify(
        {
            “prompt”: new_prompt,
            “prompt_text”: new_title,
            “left”: url_for(“static”, filename=f”images/{n_cat_l}/{n_path_l.name}“),
            “right”: url_for(“static”, filename=f”images/{n_cat_r}/{n_path_r.name}“),
            “left_cat”: n_cat_l,
            “right_cat”: n_cat_r,
        }
    )
@app.route(“/admin”)
def admin():
    with get_conn() as conn:
        rows = conn.execute(
            “SELECT category, rating FROM elo ORDER BY rating DESC”
        ).fetchall()
    return render_template(“admin.html”, rows=rows)

# --------------------------------------------------
if __name__ == "__main__":
    init_db()
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
    

