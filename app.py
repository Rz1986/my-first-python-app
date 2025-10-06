# app.py
from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello_world():
    return 'Hello, World! 欢迎使用 Python 和 Flask！'

if __name__ == '__main__':
    app.run(debug=True)
