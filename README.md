# Hermes Agent

Self-hosted, tool-using AI agent with persistent memory, skills, scheduling, and multi-platform access.

## Highlights

- Persistent memory and session continuity.
- Tool orchestration with local and remote execution backends.
- CLI and messaging gateway support.
- Skill system and long-running automation via cron.
- Works with many model providers and custom endpoints.

## Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
source ~/.zshrc
hermes
```

## Local Dev Setup

```bash
cd /Users/corn/Documents/Hermes/hermes-agent
source venv/bin/activate
scripts/run_tests.sh
```

## Most Used Commands

```bash
hermes
hermes model
hermes tools
hermes gateway
hermes setup
hermes update
hermes doctor
```

## Repository Layout

- `run_agent.py`: core agent loop.
- `cli.py`: interactive CLI.
- `model_tools.py`: tool dispatch pipeline.
- `tools/`: tool implementations.
- `gateway/`: Telegram/Discord/Slack/etc integration.
- `tests/`: test suite.

## Documentation

- https://hermes-agent.nousresearch.com/docs/
- `AGENTS.md`
- `CONTRIBUTING.md`

## License

MIT. See `LICENSE`.
