"""python -m app.cli create-admin <username> [password]  |  python -m app.cli gate  (print hidden login path)"""
import secrets, sys, time
from . import auth, db


def main():
    db.init()
    if len(sys.argv) >= 3 and sys.argv[1] == "create-admin":
        user = sys.argv[2]
        pw = sys.argv[3] if len(sys.argv) > 3 else secrets.token_urlsafe(12)
        if db.q1("SELECT 1 FROM users WHERE username=?", (user,)):
            db.ex("UPDATE users SET pw_hash=?, role='admin', active=1 WHERE username=?", (auth.hash_pw(pw), user))
        else:
            db.ex("INSERT INTO users(username,pw_hash,role,active,created) VALUES(?,?,?,?,?)",
                  (user, auth.hash_pw(pw), "admin", 1, time.time()))
        print(f"admin: {user}\npassword: {pw}")
    elif len(sys.argv) >= 2 and sys.argv[1] == "gate":
        from .main import gate_path
        print("/s/" + gate_path())
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
