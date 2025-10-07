import os
import random
import re
from datetime import datetime, timedelta
from typing import Optional

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, or_, text, inspect as sa_inspect
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = (
    os.environ.get("DATABASE_URL")
    or f"sqlite:///{os.path.join(BASE_DIR, 'app.db')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


class PlayHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    game_id = db.Column(db.Integer, db.ForeignKey("game.id"), nullable=False)
    played_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Rating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    score = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    game_id = db.Column(db.Integer, db.ForeignKey("game.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "game_id", name="unique_user_game_rating"),
    )


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone_number = db.Column(db.String(20), unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    plays = db.relationship("PlayHistory", backref="user", lazy=True)
    ratings = db.relationship("Rating", backref="user", lazy=True)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    slug = db.Column(db.String(160), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=False)
    instructions = db.Column(db.Text, nullable=False)
    play_markup = db.Column(db.Text, nullable=False)
    python_code = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    plays = db.relationship("PlayHistory", backref="game", lazy=True)
    ratings = db.relationship("Rating", backref="game", lazy=True)

    @property
    def average_rating(self) -> float:
        if not self.ratings:
            return 0.0
        return sum(r.score for r in self.ratings) / len(self.ratings)


class PhoneVerification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone_number = db.Column(db.String(20), nullable=False, index=True)
    code = db.Column(db.String(6), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


@login_manager.user_loader
def load_user(user_id: str) -> Optional[User]:
    return db.session.get(User, int(user_id))


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", title).strip("-")
    return slug.lower() or "game"


def normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def ensure_user_phone_column() -> None:
    inspector = sa_inspect(db.engine)
    column_names = {column["name"] for column in inspector.get_columns("user")}
    if "phone_number" not in column_names:
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE user ADD COLUMN phone_number VARCHAR(20)"))


@app.route("/")
def index():
    games_query = (
        db.session.query(
            Game,
            func.coalesce(func.avg(Rating.score), 0).label("avg_score"),
            func.count(Rating.id).label("rating_count"),
        )
        .outerjoin(Rating)
        .group_by(Game.id)
        .order_by(func.coalesce(func.avg(Rating.score), 0).desc(), Game.created_at.desc())
    )
    game_entries = games_query.all()
    featured_games = game_entries[:10]
    return render_template(
        "index.html",
        games=game_entries,
        featured_games=featured_games,
    )


@app.route("/developer")
def developer():
    return render_template("developer.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        phone = normalize_phone(request.form.get("phone", ""))
        verification_code = request.form.get("verification_code", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not email or not phone or not password:
            flash("请完整填写注册信息。", "danger")
        elif not re.fullmatch(r"1\d{10}", phone):
            flash("请输入有效的 11 位中国大陆手机号。", "danger")
        elif password != confirm_password:
            flash("两次输入的密码不一致。", "danger")
        else:
            existing_user = User.query.filter(
                (User.username == username) | (User.email == email) | (User.phone_number == phone)
            ).first()
            if existing_user:
                flash("用户名、邮箱或手机号已被使用。", "danger")
                return render_template("register.html")

            verification = (
                PhoneVerification.query.filter_by(phone_number=phone, code=verification_code)
                .order_by(PhoneVerification.created_at.desc())
                .first()
            )
            if not verification or verification.created_at < datetime.utcnow() - timedelta(minutes=10):
                flash("验证码无效或已过期，请重新获取。", "danger")
                return render_template("register.html")

            user = User(username=username, email=email, phone_number=phone)
            user.set_password(password)
            if User.query.count() == 0:
                user.is_admin = True
            db.session.add(user)
            db.session.delete(verification)
            db.session.commit()
            flash("注册成功，请登录开始游戏之旅！", "success")
            return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        account = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter(or_(User.username == account, User.phone_number == account)).first()
        if user and user.check_password(password):
            login_user(user)
            flash("欢迎回来，游戏玩家！", "success")
            return redirect(url_for("index"))
        flash("登录失败，请检查用户名或密码。", "danger")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("您已成功退出登录。", "info")
    return redirect(url_for("index"))


@app.route("/games/new", methods=["GET", "POST"])
@login_required
def create_game():
    if not current_user.is_admin:
        flash("只有管理员可以发布新游戏。", "warning")
        return redirect(url_for("index"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        slug = request.form.get("slug", "").strip()
        description = request.form.get("description", "").strip()
        instructions = request.form.get("instructions", "").strip()
        play_markup = request.form.get("play_markup", "").strip()
        python_code = request.form.get("python_code", "").strip()

        if not title or not description or not instructions or not play_markup or not python_code:
            flash("请完整填写所有游戏信息。", "danger")
        else:
            if not slug:
                slug = slugify(title)
            elif Game.query.filter_by(slug=slug).first():
                flash("该自定义链接已存在，请使用其他链接。", "danger")
                return render_template("admin_new_game.html")

            if Game.query.filter_by(slug=slug).first():
                flash("同名链接已存在，请使用其他名称。", "danger")
                return render_template("admin_new_game.html")

            game = Game(
                title=title,
                slug=slug,
                description=description,
                instructions=instructions,
                play_markup=play_markup,
                python_code=python_code,
            )
            db.session.add(game)
            db.session.commit()
            flash("游戏发布成功！", "success")
            return redirect(url_for("game_detail", slug=slug))

    return render_template("admin_new_game.html")


@app.route("/games/<slug>")
def game_detail(slug: str):
    game = Game.query.filter_by(slug=slug).first_or_404()
    avg_rating = (
        db.session.query(func.coalesce(func.avg(Rating.score), 0))
        .filter(Rating.game_id == game.id)
        .scalar()
    )
    user_rating = None
    if current_user.is_authenticated:
        user_rating = Rating.query.filter_by(user_id=current_user.id, game_id=game.id).first()
    return render_template(
        "game_detail.html",
        game=game,
        avg_rating=round(float(avg_rating), 2) if avg_rating else 0,
        user_rating=user_rating,
    )


@app.route("/games/<slug>/play")
@login_required
def play_game(slug: str):
    game = Game.query.filter_by(slug=slug).first_or_404()
    record_play(game)
    return render_template("game_play.html", game=game)


@app.route("/games/<slug>/rate", methods=["POST"])
@login_required
def rate_game(slug: str):
    game = Game.query.filter_by(slug=slug).first_or_404()
    try:
        score = int(request.form.get("score", 0))
    except ValueError:
        score = 0

    if score < 1 or score > 5:
        flash("评分范围为 1-5 分。", "danger")
        return redirect(url_for("game_detail", slug=slug))

    rating = Rating.query.filter_by(user_id=current_user.id, game_id=game.id).first()
    if rating:
        rating.score = score
        rating.created_at = datetime.utcnow()
    else:
        rating = Rating(score=score, user_id=current_user.id, game_id=game.id)
        db.session.add(rating)
    db.session.commit()
    flash("感谢您的评分！", "success")
    return redirect(url_for("game_detail", slug=slug))


@app.route("/history")
@login_required
def history():
    plays = (
        PlayHistory.query.filter_by(user_id=current_user.id)
        .order_by(PlayHistory.played_at.desc())
        .all()
    )
    return render_template("history.html", plays=plays)


def record_play(game: Game) -> None:
    play = PlayHistory(user_id=current_user.id, game_id=game.id)
    db.session.add(play)
    db.session.commit()


@app.route("/register/send_code", methods=["POST"])
def send_phone_code():
    data = request.get_json(silent=True) or {}
    phone_raw = data.get("phone", "")
    phone = normalize_phone(phone_raw)

    if not phone:
        return jsonify({"ok": False, "message": "请输入手机号后再获取验证码。"}), 400

    if not re.fullmatch(r"1\d{10}", phone):
        return jsonify({"ok": False, "message": "手机号格式不正确，请输入 11 位号码。"}), 400

    code = f"{random.randint(0, 999999):06d}"
    verification = PhoneVerification(phone_number=phone, code=code)
    db.session.add(verification)
    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "message": "验证码已发送（演示环境请直接使用下方验证码）。",
            "code": code,
        }
    )


@app.context_processor
def inject_now():
    return {"current_year": datetime.utcnow().year}


def seed_data() -> None:
    ensure_user_phone_column()
    if not User.query.filter_by(username="admin").first():
        admin = User(
            username="admin",
            email="admin@example.com",
            phone_number="13800000000",
            is_admin=True,
        )
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()
    else:
        admin = User.query.filter_by(username="admin").first()
        if admin and not admin.phone_number:
            admin.phone_number = "13800000000"
            db.session.commit()

    default_games = [
        {
            "title": "贪吃蛇大作战",
            "slug": "snake-classic",
            "description": "操控小蛇吃到更多能量块，身体越来越长，挑战自己的反应力。",
            "instructions": "使用方向键移动，按空格重新开始，不要撞到自己！",
            "play_markup": """
<div class=\"game-panel\">
  <h2>贪吃蛇大作战</h2>
  <canvas id=\"snake-canvas\" width=\"300\" height=\"300\"></canvas>
  <p id=\"snake-status\">按任意方向键开始移动，按空格重新开始。</p>
</div>
""".strip(),
            "python_code": """
import random
from js import document, window
from pyodide.ffi import create_proxy

canvas = document.getElementById('snake-canvas')
ctx = canvas.getContext('2d')
status_el = document.getElementById('snake-status')

grid_size = 20
cell_size = canvas.width // grid_size

snake = [(10, 10)]
direction = (0, 0)
food = None
interval_id = None
step_proxy = None
key_proxy = None


def place_food():
    global food
    while True:
        candidate = (random.randint(0, grid_size - 1), random.randint(0, grid_size - 1))
        if candidate not in snake:
            food = candidate
            break


def draw():
    ctx.fillStyle = '#101820'
    ctx.fillRect(0, 0, canvas.width, canvas.height)

    ctx.fillStyle = '#ff5555'
    if food:
        ctx.fillRect(food[0] * cell_size, food[1] * cell_size, cell_size - 1, cell_size - 1)

    ctx.fillStyle = '#50fa7b'
    for x, y in snake:
        ctx.fillRect(x * cell_size, y * cell_size, cell_size - 1, cell_size - 1)


def stop_game():
    global interval_id
    if interval_id is not None:
        window.clearInterval(interval_id)
        interval_id = None


def step():
    global snake
    if direction == (0, 0):
        return

    head_x, head_y = snake[0]
    new_head = ((head_x + direction[0]) % grid_size, (head_y + direction[1]) % grid_size)

    if new_head in snake:
        status_el.innerText = '碰到自己啦！按空格重新开始挑战。'
        stop_game()
        return

    snake.insert(0, new_head)
    if food and new_head == food:
        place_food()
    else:
        snake.pop()

    draw()


def start_game():
    global interval_id, step_proxy
    if interval_id is None:
        if step_proxy is None:
            step_proxy = create_proxy(step)
        interval_id = window.setInterval(step_proxy, 160)


def reset():
    global snake, direction
    snake = [(10, 10)]
    direction = (0, 0)
    place_food()
    draw()
    status_el.innerText = '按方向键开始移动，按空格重新开始。'


def on_keydown(event):
    global direction
    key = event.key
    if key == ' ':
        stop_game()
        reset()
        start_game()
        return

    mapping = {
        'ArrowUp': (0, -1),
        'ArrowDown': (0, 1),
        'ArrowLeft': (-1, 0),
        'ArrowRight': (1, 0),
        'w': (0, -1),
        's': (0, 1),
        'a': (-1, 0),
        'd': (1, 0),
    }

    if key in mapping:
        new_direction = mapping[key]
        if len(snake) == 1 or (new_direction[0] != -direction[0] or new_direction[1] != -direction[1]):
            direction = new_direction
            start_game()


def setup():
    global key_proxy
    reset()
    key_proxy = create_proxy(on_keydown)
    document.addEventListener('keydown', key_proxy)


setup()
""".strip(),
        },
        {
            "title": "愤怒的小鸟：弹弓挑战",
            "slug": "angry-birds",
            "description": "控制发射角度和力度，将小鸟精准击中绿色小猪，体验弹道物理。",
            "instructions": "拖动滑块调整角度与力度，点击发射按钮尝试击倒小猪目标。",
            "play_markup": """
<div class=\"game-panel\">
  <h2>愤怒的小鸟：弹弓挑战</h2>
  <div class=\"game-control\">
    <label>角度：<input id=\"bird-angle\" type=\"range\" min=\"10\" max=\"80\" value=\"45\" /></label>
    <label>力度：<input id=\"bird-power\" type=\"range\" min=\"10\" max=\"100\" value=\"55\" /></label>
    <button id=\"bird-launch\" class=\"primary\">发射！</button>
  </div>
  <canvas id=\"bird-canvas\" width=\"360\" height=\"200\"></canvas>
  <p id=\"bird-status\">调整角度和力度，点击发射尝试命中目标。</p>
</div>
""".strip(),
            "python_code": """
import math
import random
from js import document
from pyodide.ffi import create_proxy

canvas = document.getElementById('bird-canvas')
ctx = canvas.getContext('2d')

angle_input = document.getElementById('bird-angle')
power_input = document.getElementById('bird-power')
launch_button = document.getElementById('bird-launch')
status_el = document.getElementById('bird-status')

gravity = 9.8
pig_x = random.randint(220, 320)
pig_y = random.randint(80, 140)
bird_path = []
launch_proxy = None


def draw_scene():
    ctx.fillStyle = '#87ceeb'
    ctx.fillRect(0, 0, canvas.width, canvas.height)

    ctx.fillStyle = '#6ab04c'
    ctx.fillRect(0, canvas.height - 30, canvas.width, 30)

    ctx.fillStyle = '#f9ca24'
    ctx.beginPath()
    ctx.arc(40, 40, 20, 0, 2 * math.pi)
    ctx.fill()

    ctx.fillStyle = '#badc58'
    ctx.beginPath()
    ctx.arc(pig_x, pig_y, 12, 0, 2 * math.pi)
    ctx.fill()

    ctx.fillStyle = '#eb4d4b'
    for x, y in bird_path:
        ctx.fillRect(x - 3, canvas.height - y - 3, 6, 6)


def launch(event):
    global bird_path, pig_x, pig_y
    angle_deg = float(angle_input.value)
    power = float(power_input.value)

    angle_rad = math.radians(angle_deg)
    velocity = power / 2
    bird_path = []

    time = 0.0
    hit = False
    while time < 5:
        x = 20 + velocity * math.cos(angle_rad) * time * 10
        y = velocity * math.sin(angle_rad) * time * 10 - 0.5 * gravity * (time ** 2) * 10
        if y < 0:
            break
        bird_path.append((x, y))
        dx = x - pig_x
        dy = y - pig_y
        if abs(dx) < 12 and abs(dy) < 12:
            hit = True
            break
        time += 0.08

    if hit:
        status_el.innerText = '好准！你成功命中了小猪，换个角度再来一次吧。'
        pig_x = random.randint(220, 320)
        pig_y = random.randint(80, 140)
    else:
        status_el.innerText = '差一点点，再调整角度或力度试试！'

    draw_scene()


def setup():
    global launch_proxy
    draw_scene()
    launch_proxy = create_proxy(launch)
    launch_button.addEventListener('click', launch_proxy)


setup()
""".strip(),
        },
        {
            "title": "俄罗斯方块：堆叠大师",
            "slug": "tetris-classic",
            "description": "控制下落的方块堆叠成完整行，消除行获得分数，越玩越上瘾。",
            "instructions": "方向键左右移动，向下加速，下落中按空格旋转方块。",
            "play_markup": """
<div class=\"game-panel\">
  <h2>俄罗斯方块：堆叠大师</h2>
  <canvas id=\"tetris-canvas\" width=\"200\" height=\"400\"></canvas>
  <p id=\"tetris-status\">消除的行数越多，分数越高，试着挑战自己的极限吧！</p>
</div>
""".strip(),
            "python_code": """
import random
from js import document, window
from pyodide.ffi import create_proxy

canvas = document.getElementById('tetris-canvas')
ctx = canvas.getContext('2d')
status_el = document.getElementById('tetris-status')

rows, cols = 20, 10
cell_size = canvas.width // cols
board = [[0 for _ in range(cols)] for _ in range(rows)]

shapes = [
    [[1, 1, 1, 1]],
    [[1, 1], [1, 1]],
    [[0, 1, 0], [1, 1, 1]],
    [[1, 1, 0], [0, 1, 1]],
    [[0, 1, 1], [1, 1, 0]],
    [[1, 0, 0], [1, 1, 1]],
    [[0, 0, 1], [1, 1, 1]],
]

colors = ['#00bcd4', '#ffbe0b', '#ff006e', '#8338ec', '#3a86ff', '#fb5607', '#2ec4b6']

current_shape = None
current_color = '#00bcd4'
shape_row = 0
shape_col = 3
drop_interval = None
step_proxy = None
key_proxy = None
score = 0


def new_shape():
    global current_shape, current_color, shape_row, shape_col
    index = random.randrange(len(shapes))
    current_shape = [row[:] for row in shapes[index]]
    current_color = colors[index]
    shape_row = 0
    shape_col = cols // 2 - len(current_shape[0]) // 2
    return valid_position()


def draw_board():
    ctx.fillStyle = '#0f172a'
    ctx.fillRect(0, 0, canvas.width, canvas.height)

    for r in range(rows):
        for c in range(cols):
            if board[r][c]:
                ctx.fillStyle = board[r][c]
                ctx.fillRect(c * cell_size, r * cell_size, cell_size - 1, cell_size - 1)

    if current_shape:
        for r, row in enumerate(current_shape):
            for c, value in enumerate(row):
                if value:
                    x = (shape_col + c) * cell_size
                    y = (shape_row + r) * cell_size
                    ctx.fillStyle = current_color
                    ctx.fillRect(x, y, cell_size - 1, cell_size - 1)


def valid_position(offset_row=0, offset_col=0, shape=None):
    check_shape = shape or current_shape
    for r, row in enumerate(check_shape):
        for c, value in enumerate(row):
            if not value:
                continue
            new_r = shape_row + r + offset_row
            new_c = shape_col + c + offset_col
            if new_c < 0 or new_c >= cols or new_r >= rows:
                return False
            if new_r >= 0 and board[new_r][new_c]:
                return False
    return True


def lock_shape():
    global score
    for r, row in enumerate(current_shape):
        for c, value in enumerate(row):
            if value:
                if shape_row + r < 0:
                    continue
                board[shape_row + r][shape_col + c] = current_color

    cleared = 0
    for r in range(rows - 1, -1, -1):
        if all(board[r]):
            cleared += 1
            del board[r]
            board.insert(0, [0 for _ in range(cols)])

    if cleared:
        score += cleared * 100
        status_el.innerText = f'不错！当前分数：{score} 分'


def rotate_shape():
    rotated = list(zip(*current_shape[::-1]))
    rotated = [list(row) for row in rotated]
    if valid_position(shape=rotated):
        return rotated
    return current_shape


def step():
    global shape_row
    if valid_position(offset_row=1):
        shape_row += 1
    else:
        if shape_row < 1:
            game_over()
            return
        lock_shape()
        if not new_shape():
            game_over()
            return
    draw_board()


def game_over():
    global current_shape
    stop()
    current_shape = None
    draw_board()
    status_el.innerText = f'游戏结束！总分 {score} 分，按回车重新开始。'


def stop():
    global drop_interval
    if drop_interval is not None:
        window.clearInterval(drop_interval)
        drop_interval = None


def start():
    global drop_interval, step_proxy
    if drop_interval is None:
        if step_proxy is None:
            step_proxy = create_proxy(step)
        drop_interval = window.setInterval(step_proxy, 500)


def handle_key(event):
    global shape_col, shape_row, current_shape
    key = event.key
    if drop_interval is None and key != 'Enter':
        return
    if key == 'ArrowLeft' and valid_position(offset_col=-1):
        shape_col -= 1
    elif key == 'ArrowRight' and valid_position(offset_col=1):
        shape_col += 1
    elif key == 'ArrowDown' and valid_position(offset_row=1):
        shape_row += 1
    elif key == ' ':
        current_shape = rotate_shape()
    elif key == 'Enter':
        reset()
        return
    draw_board()


def reset():
    global board, score
    stop()
    for r in range(rows):
        for c in range(cols):
            board[r][c] = 0
    score = 0
    status_el.innerText = '消除的行数越多，分数越高，试着挑战自己的极限吧！'
    if not new_shape():
        game_over()
        return
    draw_board()
    start()


def setup():
    global key_proxy
    key_proxy = create_proxy(handle_key)
    document.addEventListener('keydown', key_proxy)
    reset()


setup()
""".strip(),
        },
    ]

    created = False
    for data in default_games:
        if not Game.query.filter_by(slug=data["slug"]).first():
            db.session.add(Game(**data))
            created = True

    if created:
        db.session.commit()


def init_app() -> None:
    with app.app_context():
        db.create_all()
        seed_data()


init_app()


if __name__ == "__main__":
    app.run(debug=True)
