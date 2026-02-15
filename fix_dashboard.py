import re

path = r"d:\Data\git\sportsbetting-group\betfinder\app\web\templates\dashboard.html"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Fix currentPresetId
content = re.sub(
    r'let currentPresetId = \{%\s*if first_preset\s*%\}.*?\{%\s*else\s*%\}.*?\{%\s*endif\s*%\};',
    'let currentPresetId = {% if first_preset %}{{ first_preset.id }}{% else %}null{% endif %};',
    content
)

# Fix currentDefaultStake
content = re.sub(
    r'let currentDefaultStake = \{%\s*if first_preset\s*%\}.*?\{%\s*else\s*%\}.*?\{%\s*endif\s*%\};',
    'let currentDefaultStake = {% if first_preset %}{{ first_preset.default_stake or 10 }}{% else %}10{% endif %};',
    content
)

# Fix initialConfig
content = re.sub(
    r'const initialConfig = \{%\s*if first_preset\s*%\}.*?\{%\s*else\s*%\}.*?\{%\s*endif\s*%\};',
    'const initialConfig = {{ (first_preset.other_config or {}) | tojson | safe }};',
    content
)
# Also catch the case where initialConfig might not be wrapped in if in some versions, but based on recent view it was inside {% if first_preset %}
# Actually, the previous view showed:
# 219:     {% if first_preset %}
# 220:     const initialConfig = {{ (first_preset.other_config or { }) | tojson | safe }};
# So just targeting the line content is safer.

content = re.sub(
    r'const initialConfig = \{\{.*?\}\};',
    'const initialConfig = {{ (first_preset.other_config or {}) | tojson | safe }};',
    content
)

# General cleanup of { { -> {{
content = re.sub(r'\{\s+\{\s+', '{{ ', content)
content = re.sub(r'\s+\}\s+\}', ' }}', content)
content = content.replace("{{  ", "{{ ")
content = content.replace("  }}", " }}")
content = content.replace("{ {", "{{")
content = content.replace("} }", "}}")

# Fix specific dict issue
content = content.replace("or { }", "or {}")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print(f"Patched {path}")

with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()
    for i, line in enumerate(lines):
        if "currentPresetId =" in line or "currentDefaultStake =" in line or "initialConfig =" in line:
            print(f"{i+1}: {line.strip()}")
