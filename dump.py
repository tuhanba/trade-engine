import pprint

with open("app.py", "rb") as f:
    content = f.read()

with open("scratch.txt", "w") as f:
    f.write(repr(content[:500]))
