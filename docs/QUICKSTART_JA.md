# R4 プラットフォーム クイックスタート（日本語版）

> **English**: [QUICKSTART.md](./QUICKSTART.md)

この文書は英語版に対応する日本語クイックスタートです。約 30 分で最初の AI 研究プロジェクトまで進めます。

## 目次

1. 前提条件
2. セットアップ
3. ナレッジの取り込み
4. 研究プランと自律ループ
5. ナレッジの書き出し
6. トラブルシューティング

---

## 1. 前提条件

必要なソフトウェア:

| コンポーネント | バージョン | 役割 |
|---|---|---|
| Slurm | 23+（または compose モック）| ジョブスケジューラ |
| Podman または Docker | 4+ | コンテナ実行環境 |
| PostgreSQL | 15+（pgvector 必須）| メタデータ保存 |
| Python | 3.11+ | CLI 実行環境 |
| r4 CLI | 最新 | 研究オーケストレーション |

環境準備:

```bash
uv venv && source .venv/bin/activate
uv pip install -e ./r4-contracts -e ./packages/r4-teamengine-spi -e ./science -e ./r4
```

リポジトリのルートで実行します（`make install-client` でも同じパッケージが入ります）。r4 は PyPI には公開されていません。

`r4 doctor` を実行し、ゲートウェイ接続・プロファイル・モデル設定が ✓ になっていることを確認してください。初回は `r4 onboard` が推奨です。

## 2. セットアップ

```bash
# プラットフォームへログイン（API キーまたは OIDC）
r4 login

# プロジェクトの作成と選択
r4 project create perovskite-discovery
r4 project switch perovskite-discovery

# 研究ゴールの宣言（最初の研究プラン）
r4 project plan perovskite-discovery "ペロブスカイト太陽電池の候補を自動探索する"
```

## 3. ナレッジの取り込み

```bash
# (a) 文書やデータを取り込む
r4 project import perovskite-discovery ./papers ./notebooks

# (b) KnowledgeIR とグラフを構築
r4 project init perovskite-discovery

# (c) 知識ベースに質問
r4 project verify perovskite-discovery "この知識で使える材料候補は?"
```

**外部ソースの取り込みについて:**
`http_url` や `git_repo` のような外部ソースの取り込みは `KNOWLEDGE_INGEST` ジョブ内に制限され、サイト管理者が許可したドメインのみに outbound 通信を行います。ローカルで fetch する場合は明示的に `--local-fetch` を付けます（監査対象）。

## 4. 研究プランと自律ループ

### 主なコマンド

| コマンド | 役割 |
|---|---|
| `r4 project plan <id> "<goal>"` | 研究プランを作成 |
| `r4 project readiness <id>` | 実行可能性を診断 |
| `r4 project approve <id>` | プランを承認 |

### 自律ループの管理

```bash
r4 project loop start <id> "<goal>" --approve   # ループを開始
r4 project loop watch <loop_id>                 # 進捗を監視
r4 project loop status <loop_id>                # 状態を確認
r4 project loop pause <loop_id>                 # 一時停止
r4 project loop resume <loop_id>                # 再開
r4 project loop stop <loop_id>                  # 終了
```

詳細は `docs/research-workflow.md` を参照してください。

## 5. ナレッジの書き出し

整備した知識は `.kb` 形式に export でき、ほかの環境で使い回せます（INV-13）:

```bash
r4 knowledge export perovskite-discovery > perovskite-discovery.kb
r4 knowledge import perovskite-discovery.kb
```

## 6. トラブルシューティング

| 症状 | 対処 |
|---|---|
| Gateway に接続できない | `r4 doctor` で診断 → `r4 login` を実行 |
| モデルが見つからない | `r4 model list` で確認（system 所有モデルが表示されるかを確認） |
| ループが resume しない | C2 で追加された durability パッチを適用済みかを確認（W4a=TN05） |
| エグレスの付与希望が拒否される | EgressGuard のポリシー確認。サイト管理者が allowed_domains の交渉 |

エラーメッセージの `hint:` 箇所が最初の手がかりです。
