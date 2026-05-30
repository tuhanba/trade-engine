import sys
with open('core/ai_decision_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('setup_quality in ("A", "B") and confluence >= 2', 'setup_quality in ("A", "B", "C") and confluence >= 1')
content = content.replace('setup_quality not in ("S", "A+"):', 'setup_quality not in ("S", "A+", "A", "B"):')

with open('core/ai_decision_engine.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Patched successfully')
