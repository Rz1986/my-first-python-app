import os
import re
from datetime import datetime
from typing import Optional

from flask import (
    Flask,
    flash,
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
from sqlalchemy import func
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


@login_manager.user_loader
def load_user(user_id: str) -> Optional[User]:
    return db.session.get(User, int(user_id))


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", title).strip("-")
    return slug.lower() or "game"


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
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not email or not password:
            flash("请完整填写注册信息。", "danger")
        elif password != confirm_password:
            flash("两次输入的密码不一致。", "danger")
        elif User.query.filter((User.username == username) | (User.email == email)).first():
            flash("用户名或邮箱已被使用。", "danger")
        else:
            user = User(username=username, email=email)
            user.set_password(password)
            if User.query.count() == 0:
                user.is_admin = True
            db.session.add(user)
            db.session.commit()
            flash("注册成功，请登录开始游戏之旅！", "success")
            return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
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


@app.context_processor
def inject_now():
    return {"current_year": datetime.utcnow().year}


def seed_data() -> None:
    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", email="admin@example.com", is_admin=True)
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()

    if not Game.query.first():
        guess_markup = """
<div class=\"game-panel\">
  <h2>猜数字小游戏</h2>
  <p>系统随机生成 1 到 20 的数字，猜猜它是多少吧！</p>
  <div class=\"game-control\">
    <input id=\"guess-input\" type=\"number\" min=\"1\" max=\"20\" placeholder=\"输入你的猜测\" />
    <button id=\"guess-button\" class=\"primary\">提交答案</button>
  </div>
  <p id=\"message\">祝你好运！</p>
</div>
""".strip()
        guess_code = """
import random
from js import document

if 'secret_number' not in globals():
    secret_number = random.randint(1, 20)

input_el = document.getElementById('guess-input')
message_el = document.getElementById('message')
button_el = document.getElementById('guess-button')

def check_guess(event):
    try:
        guess = int(input_el.value)
    except ValueError:
        message_el.innerText = '请输入 1-20 的整数！'
        return

    if guess == secret_number:
        message_el.innerText = '恭喜你猜对啦！刷新页面重新挑战。'
    elif guess < secret_number:
        message_el.innerText = '再大一点！'
    else:
        message_el.innerText = '再小一点！'

button_el.addEventListener('click', check_guess)
""".strip()
        game = Game(
            title="猜数字挑战",
            slug="guess-number",
            description="经典的猜数字游戏，考验你的直觉和运气。",
            instructions="输入 1-20 的数字并提交，看看你能否用最少次数猜中！",
            play_markup=guess_markup,
            python_code=guess_code,
        )
        db.session.add(game)
        db.session.commit()


def init_app() -> None:
    with app.app_context():
        db.create_all()
        seed_data()


init_app()


if __name__ == "__main__":
    app.run(debug=True)
