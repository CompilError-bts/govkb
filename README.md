# GovKB

GovKB is a Python CLI tool for building a local knowledge base from Chinese government portal news. Given a government website URL, it discovers the news section, scrapes recent articles, extracts article bodies, and writes one `.docx` file per article.

The current implementation focuses on URL-driven workflows:

```bash
python -m govkb.main run --url www.xianyang.gov.cn --months 3
```

## What It Does

- Analyzes a government portal homepage and finds a suitable news column.
- Verifies the detected list page by extracting article title, URL, and date.
- Scrapes recent articles by date range.
- Fetches article body text.
- Generates formatted Word documents.

If no LLM API key is configured, GovKB uses deterministic heuristics and verification. If an LLM is configured, it asks the model to analyze the HTML structure first, then still verifies the result by real fetching.

## Install

```bash
pip install -r requirements.txt
```

## Quick Start

Run a small smoke test against Xianyang with only one list page:

```bash
python -m govkb.main run --url www.xianyang.gov.cn --months 1 --max-pages 1
```

Run the normal 3-month workflow:

```bash
python -m govkb.main run --url https://www.xianyang.gov.cn --months 3
```

Launch the GUI:

```bash
python -m govkb.gui
```

The GUI can show the agent trace, including recipe hits, candidate news columns tried, off-site columns skipped, and whether the final result came from the agent or the legacy fallback.

Generated documents are written under `output/`, for example:

```text
output/咸阳市人民政府_新闻_2026-04-19起/
```

## Configuration

Default settings live in `config.yaml`.

LLM disabled:

```yaml
llm:
  provider: none
```

OpenAI-compatible API:

```yaml
llm:
  provider: openai
  api_key: ${OPENAI_API_KEY}
  model: gpt-4o-mini
  base_url: https://api.openai.com/v1
```

DeepSeek API:

```yaml
llm:
  provider: openai
  api_key: ${DEEPSEEK_API_KEY}
  model: deepseek-v4-flash
  base_url: https://api.deepseek.com/v1
```

DeepSeek's legacy model names `deepseek-chat` and `deepseek-reasoner` are scheduled for deprecation on 2026-07-24, so new configs should use `deepseek-v4-flash` or `deepseek-v4-pro`.

Claude API:

```yaml
llm:
  provider: claude
  api_key: ${ANTHROPIC_API_KEY}
  model: claude-3-5-sonnet-latest
  base_url: https://api.anthropic.com/v1
```

Use a different config file:

```bash
python -m govkb.main --config path/to/config.yaml run --url www.xianyang.gov.cn --months 3
```

## CLI

```bash
python -m govkb.main run --url <government-site-url> [--months 3] [--max-pages 30]
```

Options:

- `--url`: Government portal URL.
- `--months`: Recent months to scrape. Internally approximated as `months * 30` days.
- `--max-pages`: Maximum list pages to scan. Useful for testing.
- `--config`: Path to config file. Defaults to `config.yaml`.

`--city` requires an LLM config. GovKB asks the model for official government portal candidates, verifies that the returned URL is reachable and under a `gov.cn` domain, then continues with the normal workflow.

```bash
python -m govkb.main run --city 青岛市政府 --months 1 --max-pages 1
```

## Architecture

```text
govkb/
├── main.py
├── core/
│   ├── orchestrator.py
│   ├── site_analyzer.py
│   ├── scraper.py
│   └── docx_generator.py
├── llm/
│   ├── client.py
│   └── prompts.py
├── models/
│   ├── site_info.py
│   └── article.py
└── utils/
    └── http.py
```

## Verified Example

The following command was verified during development:

```bash
python -m govkb.main run --url www.xianyang.gov.cn --months 1 --max-pages 1
```

It identified:

- Portal: `咸阳市人民政府`
- News section: `本地要闻`
- List URL: `https://www.xianyang.gov.cn/xyxw/xyxw_14/`
- Generated documents: `24/24`

## Notes

- Government sites vary heavily. GovKB combines LLM analysis, heuristic fallback, and real-page verification to handle structural differences.
- Official website URLs come from either a URL entered by the user or, for city/name input, an LLM-generated candidate that GovKB verifies before scraping.
- Please keep `rate_limit` conservative when scraping public websites.
