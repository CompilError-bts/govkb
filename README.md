# GovKB

GovKB is a Python tool for building a local knowledge base from Chinese government portal news. Given a government website URL or a place name, it discovers the official portal, identifies a news section, scrapes recent articles, extracts article bodies, and writes one `.docx` file per article.

## Quick Start

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the CLI:

```bash
python -m govkb.main run --url https://www.xianyang.gov.cn --months 3
```

Run by place name with an LLM configured:

```bash
python -m govkb.main run --city 青岛市政府 --months 3
```

Launch the desktop GUI:

```bash
python -m govkb.gui
```

Launch the local WebUI:

```bash
python -m govkb.main webui
```

The GUI and WebUI save provider, model, API key, output directory, and recipe directory in a local user settings file. On Windows this is under `%APPDATA%\GovKB\settings.json`. The API key is not written into the project repository, but it is stored locally in plain text, so use this only on a trusted machine.

Generated documents are written under `output/`, for example:

```text
output/咸阳市人民政府_新闻_2026-04-19起
```

If the user input contains a Chinese place name, GovKB keeps that region signal in the generated directory name even when the resolved site is represented by a domain.

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
python -m govkb.main run --city <place-name> [--months 3] [--max-pages 30]
python -m govkb.main webui [--host 127.0.0.1] [--port 8765]
```

Options:

- `--url`: Government portal URL.
- `--city`: Place name. Requires an LLM config so GovKB can propose official portal candidates.
- `--months`: Recent months to scrape. Internally approximated as `months * 30` days.
- `--max-pages`: Maximum list pages to scan. Useful for testing.
- `--config`: Path to config file. Defaults to `config.yaml`.

## Architecture

```text
govkb/
├── main.py
├── gui.py
├── webui.py
├── core/
│   ├── orchestrator.py
│   ├── site_analyzer.py
│   ├── scraper.py
│   └── docx_generator.py
├── agent/
│   ├── site_agent.py
│   └── tools.py
├── llm/
│   ├── client.py
│   └── prompts.py
├── models/
└── utils/
```

## Notes

- Official website URLs come from either a URL entered by the user or, for place-name input, LLM-generated candidates that GovKB verifies before scraping.
- Pagination patterns are saved only when observed from real page links, dynamic APIs, or page scripts.
- Recipes are cached under `recipes/`; use the GUI/WebUI cache clear button when a site recipe needs to be rediscovered.
- Please keep `rate_limit` conservative when scraping public websites.
