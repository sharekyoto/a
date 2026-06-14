# CLAUDE.md — Ask the World / きみの「なぜ」から、師匠を探す（引き継ぎ・運用書）

> このファイルはプロジェクトの「唯一の真実(source of truth)」。
> 作業開始時に必ず読み、`git status -sb` と `git log --oneline -10` で実状態を確認してから動く。
> 食い違ったらリポジトリが正。破壊的操作(force push・大量削除・secret操作)は事前承認。

最終更新: 2026-06（日本網羅へ転換＋検索成長まで実装した状態）

---

## 1. プロダクトの核（外さない）
- **対象**: 日本の高校1・2年生(15〜17歳)。
- **狙い**: 点数でなく「心が動く問い／気になる言葉」から、いま実在する研究者(師匠)に出会い、自分の興味で進路を選べるようにする。
- **死守する制約**: 訪問者は**完全無料・広告なし・登録不要・ブラウザに鍵を出さない・2040年まで持続**。
- **必達**: キーワード検索で「ぴったりの先生」が出る(分野ざっくりでなくトピック単位)。

## 2. 場所
- 公開URL: https://sharekyoto.github.io/a/ ／ リポジトリ: github.com/sharekyoto/a (main, GitHub Pages)
- ローカル: `/Users/toshiyo/Desktop/Claude fable/toi-univ`
- 連絡先/OpenAlex mail: sharekyoto@gmail.com
- Secrets: **`OpenAlex`**(無料APIキー), **`GEMINI`**(任意・要約用の無料キー)

## 3. 重要な設計判断（このプロジェクトの方向転換）
- **2026-06に「全世界・薄く」→「日本・網羅」へ転換**(オーナー要望)。国公立+主要私立を網羅し教授クラスを深く。
- **ブラウザからライブAPIを叩かない**。データは夜間ビルドで作り置き(静的JSON)、フロントはそれをブラウザ内で照合するだけ。鍵は GitHub Actions だけ。
- **OpenAlex課金はリクエスト単位**(結果件数では課金しない)。無料枠$1/日。認証は **api_key URLパラメータ**(Bearerではない)。日本フルビルドは約520コールで無料枠内。
- Gemini無料枠(Flash-Lite, 1500req/日, カード不要)。**Billingを紐付けない**限り課金されない。

## 4. データパイプライン
```
data/universities_seed.csv (日本259校: 国立98/公立99/私立62)
  │ scripts/build_master.py  … 各校をOpenAlex機関IDに解決(api_key)。非JP誤解決は弾く。
  ▼
data/institutions.json  … {generated,count,coverage,institutions:[{id,resolved_name,name_ja,prefecture,country,...}]}
  │ scripts/grow_from_searches.py … 訪問者が検索した未収録校を追加(Phase2b)
  │ scripts/sync_researchers.py   … 各校の上位N名を取得→正規化→差分→archive
  ▼
data/researchers.json  … 全件(archived含む・消さない設計)
data/site_data.json    … フロント用(active・フラット配列)。約39,448人。19MB。
data/summaries.json    … 研究者ごとの日本語AI要約(任意・少しずつ生成)
data/growth.json       … 自動拡大の状態(max_per_inst を毎晩 step ずつ ceiling=600 まで)
data/search_demand.json… 検索需要の記録(キーワード辞書を実ログで育てる材料)
```
- site_dataレコード: `{id,name,inst,country,works,cited,h,topics:[英語],fields:[分野ID],orcid}`
- `name` は OpenAlex のローマ字。`display_name_alternatives` に漢字があれば優先(pick_name)。
- `country=None` が一部残存(過去ビルドの値保持)。content_hashにcountry追加済みで次回sync自己修復。

## 5. フロントエンド（index.html・1ファイル完結）
- ヒーロー: 「好奇心は、いちばんの才能だ／きみの『なぜ』から、師匠を探す」。"このサイトについて"でAbout(透明なFAQ＋熱い想い)。
- **成長メーター**(ヘッダ下): 1カ国・240校・47都道府県・約39,448人。データが育つと自動で増える。
- **起動高速化**: 軽いJSON(questions/lexicon/institutions/summaries)で即描画、19MBのsite_dataは裏で先読み。検索時に `ensureSite()` で待つ。
- 4つの探し方:
  1. **キーワード**(既定): `data/lexicon.json` で 日本語/文章 → field/keyword に橋渡し。**段階ランキング**(具体トピック一致を優先、5件以上あれば分野だけ一致は出さない)＋**英語語は単語境界(\b)照合**。これで「お笑い→6人」のようにドンピシャに。
  2. **問い**: `data/questions.json`(8クラスタ28問・大人トーン)→ field → 研究者。
  3. **学校・研究者名**: institutions.json で校名(和/英)、site_dataで研究者名(メモリ内・追加APIなし)。
  4. カードの名前クリック → **Google AIモード**(udm=50)で深掘り。指標は「活躍度」風の温かい日本語(h指数・英語topicは非表示。要約あればそれを主役)。
- **がっかりさせない**: 検索が薄い/空のとき「また来てね・次の更新まで約N時間」(実時刻計算)。
- **検索成長(Phase2b)**: `logSearch()` が検索語を無料Googleフォームに記録(SEARCH_LOG_URL/FIELD設定済・no-cors)。夜間ジョブが探された校を追加。

## 6. CI / ワークフロー
- **sync-researchers.yml**(夜間03:00JST＋手動): grow_from_searches → sync_researchers。`OpenAlex`キー、MAX_PER_INST=200、growth.jsonで自動拡大。data自動commit。
- **summaries.yml**(夜間05:00JST＋手動): generate_summaries.py。`GEMINI`キー、DAILY_CAP(手動既定200/自動800)。被引用上位から日本語要約を少しずつ。
- **initial-build.yml**(手動専用): build_master→sync のフル再構築。`OpenAlex`キー。push自動起動なし。
- weekly-sync.yml は削除済み。

## 7. 未完了・次の候補
1. **lazyweb デザイン刷新**(現タスク): 配色/タイポ/モーション/レイアウトを高校生が惹かれる見た目に。機能(4つの探し方・成長メーター・About・深掘りリンク)を壊さないこと。1ファイルCSS完結・CDNのみ・localStorage不可。
2. **キーワード辞書を実ログで育てる**: `data/search_demand.json` を見て、よく検索される語の日↔英マッピングを `lexicon.json` の keywords に足す(精度の本丸・無料・軽い)。
3. 広い分野の件数は多いが全員関連(上位がドンピシャ)。真の意味検索が要るなら**ブラウザ内埋め込み**(無料・鍵不要だがモデルDLで重い)が将来オプション。
4. 漢字名の取得拡大(researchmap等)、country=None の解消(次回sync)。
5. 将来機能: 研究者名→論文(OpenAlex)・図書(OpenLibrary)・researchmap リンクアウト(全て無料・鍵不要)。

## 8. 運用メモ
- **commit/pushはオーナーのMacターミナルにコピペで実行**(サンドボックスは.gitロックを消せず不安定)。push前に必ず `git pull --rebase --autostash origin main`(夜間botのcommitと競合回避)。ロック詰まりは `rm -f .git/index.lock .git/HEAD.lock`。
- フロント編集後は `node --check`(script抽出)で構文確認。データ整形は `Path.write_text` で確実に(inline open()のflush漏れでJSON破損した事故あり)。
- 破壊的変更前は `git switch -c <branch>`。
