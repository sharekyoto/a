# Ask the World / 問いから探す大学検索

心が動く「問い」から、世界の研究者(師匠)に出会えるバイリンガル検索サイト。

## 🚀 Links
- **Live**: https://sharekyoto.github.io/a/
- **Data**: OpenAlex (free, no key required)

## 📦 Local Development
```bash
bash publish_local.sh           # Generate data
cd upload && python3 -m http.server 8000
# http://localhost:8000
```

## 🏗️ Architecture
- **index.html** — bilingual interface
- **data/questions.json** — 28 questions × 8 clusters
- **data/site_data.json** — researchers (auto-updated weekly)
- **scripts/** — data pipeline (no Gemini on critical path)

Contact: sharekyoto@gmail.com
