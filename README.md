# Wikipedia Citation & Cleanup Tool

A tool that identifies short Wikipedia articles lacking citations, makes **minimal corrective edits** (grammar, style, policy compliance, wikilinks), and **adds references to existing claims**—without introducing new substantive content. All edits require human review before submission.

## Overview

This tool helps Wikipedia editors by automating the tedious work of:
- Finding reliable sources for existing claims in stub articles
- Fixing grammar, spelling, and style issues per Wikipedia's Manual of Style
- Adding appropriate wikilinks to existing text
- Correcting policy violations (NPOV, formatting, etc.)

### What This Tool Does

✅ Finds verifiable sources for **claims already present** in articles
✅ Fixes grammar, spelling, and style issues per MOS
✅ Adds appropriate wikilinks to existing text
✅ Corrects policy violations
✅ Formats existing content properly

### What This Tool Does NOT Do

❌ Add new facts, sentences, or paragraphs
❌ Expand article scope or coverage
❌ Rewrite content beyond minimal corrections
❌ Remove content (except clear policy violations)

## Features

- **Article Picker**: Automatically finds stub articles that need citations
- **Claude AI Agent**: Analyzes articles and proposes minimal edits
- **Source Finder**: Searches academic databases for reliable sources
- **Edit Guardrails**: Ensures all edits remain minimal and policy-compliant
- **Review GUI**: Web interface for human review of all proposed edits
- **Wikipedia Integration**: Pushes approved edits with proper attribution

## Installation

### Prerequisites

- Python 3.9 or higher
- An Anthropic API key (for Claude)
- Optional: Wikipedia bot account (for pushing edits)

### Setup

1. Clone the repository:
```bash
git clone https://github.com/yourorg/wiki-cite.git
cd wiki-cite
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

Or install in development mode:
```bash
pip install -e .
```

3. Create a `.env` file from the example:
```bash
cp .env.example .env
```

4. Edit `.env` and add your API keys:
```bash
ANTHROPIC_API_KEY=your_api_key_here
WIKIPEDIA_USERNAME=your_bot_username  # Optional
WIKIPEDIA_PASSWORD=your_bot_password  # Optional
```

## Usage

### Command Line Interface

The tool provides several CLI commands:

#### 1. Fetch Candidate Articles

Find Wikipedia articles that need citations:

```bash
wiki-cite fetch --limit 10
```

This will list up to 10 stub articles that lack citations.

#### 2. Analyze a Specific Article

Analyze a Wikipedia article and get edit suggestions:

```bash
wiki-cite analyze "Groveland Four"
```

This will:
- Fetch the article from Wikipedia
- Analyze it with Claude
- Display proposed edits with rationales

#### 3. Start the Web Interface

Launch the web-based review interface:

```bash
wiki-cite web
```

Then open your browser to `http://localhost:5000`

Options:
- `-p, --port`: Port to run on (default: 5000)
- `-H, --host`: Host to bind to (default: 0.0.0.0)
- `-d, --debug`: Enable debug mode

#### 4. View Configuration

See your current configuration:

```bash
wiki-cite config
```

### Web Interface

The web interface provides a visual workflow for reviewing edits:

1. **Fetch Article**: Click "Fetch New Article" to get a candidate article
2. **Review Edits**: See all proposed edits with diffs and rationales
3. **Approve/Reject**: Individually approve or reject each edit
4. **Preview**: View combined diff of all approved edits
5. **Push**: Submit approved edits to Wikipedia

Each edit shows:
- **Type**: Citation, Grammar, Style, Wikilink, Policy, or Formatting
- **Confidence**: High, Medium, or Low
- **Diff**: Side-by-side view of original vs. proposed text
- **Rationale**: Explanation for the change
- **Policy Reference**: Link to relevant Wikipedia policy

## Configuration

Edit `config.yaml` to customize behavior:

```yaml
agent:
  model: "claude-sonnet-4-20250514"
  max_edits_per_article: 15

guardrails:
  max_new_words: 50           # Excluding citations/templates
  max_content_removal_pct: 20
  min_similarity_ratio: 0.85
  skip_blp_articles: true

sources:
  search_apis:
    - semantic_scholar
    - crossref
  reliability_check: true

wikipedia:
  edit_summary_suffix: "(AI-assisted citation/cleanup, human-reviewed)"
  rate_limit_edits_per_hour: 10

article_selection:
  category: "Category:Articles_lacking_sources"
  max_body_lines: 4
  exclude_blp: true
  exclude_protected: true
```

### Configuration Options

**Agent Settings:**
- `model`: Claude model to use for analysis
- `max_edits_per_article`: Maximum edits to propose per article

**Guardrails:**
- `max_new_words`: Maximum new words allowed (excludes citations)
- `max_content_removal_pct`: Maximum content that can be removed (%)
- `min_similarity_ratio`: Minimum similarity between original and edited (0-1)
- `skip_blp_articles`: Skip Biographies of Living Persons (recommended: true)

**Sources:**
- `search_apis`: List of APIs to search for sources
- `reliability_check`: Verify sources against Wikipedia's reliable sources list

**Wikipedia:**
- `edit_summary_suffix`: Suffix for all edit summaries
- `rate_limit_edits_per_hour`: Maximum edits per hour

**Article Selection:**
- `category`: Wikipedia category to search
- `max_body_lines`: Maximum article length to consider
- `exclude_blp`: Exclude biographies of living persons
- `exclude_protected`: Exclude protected pages

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Human Reviewer (Web GUI)                   │
│         Reviews diffs, approves/rejects/adjusts edits           │
└─────────────────────────────┬───────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────────┐
         ▼                    ▼                        ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Article Picker │  │  Claude Agent   │  │  Wikipedia API  │
│                 │  │  (Minimal Edit) │  │  Push Service   │
└─────────────────┘  └─────────────────┘  └─────────────────┘
         │                    │                        │
         ▼                    ▼                        ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ Source Finder   │  │  Guardrails     │  │  Rate Limiter   │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

### Components

1. **Article Picker** (`article_picker.py`): Selects stub articles from Wikipedia that need citations
2. **Claude Agent** (`agent.py`): Analyzes articles and proposes minimal edits using Claude AI
3. **Source Finder** (`source_finder.py`): Searches for reliable sources to verify claims
4. **Guardrails** (`guardrails.py`): Validates that edits are minimal and safe
5. **Review GUI** (`web_app.py`, `templates/`): Web interface for human review
6. **Wikipedia Push** (`wikipedia_push.py`): Submits approved edits to Wikipedia

## Development

### Running Tests

```bash
pytest tests/
```

### Type Checking

```bash
mypy wiki_cite/
```

### Project Structure

```
wiki-cite/
├── wiki_cite/              # Main package
│   ├── __init__.py
│   ├── agent.py           # Claude AI agent
│   ├── article_picker.py  # Article selection
│   ├── cli.py             # Command-line interface
│   ├── config.py          # Configuration management
│   ├── guardrails.py      # Edit validation
│   ├── models.py          # Data models
│   ├── source_finder.py   # Source search
│   ├── web_app.py         # Flask web app
│   ├── wikipedia_push.py  # Wikipedia API
│   └── templates/         # HTML templates
│       ├── base.html
│       ├── index.html
│       └── review.html
├── tests/                 # Test suite
├── config.yaml            # Configuration
├── requirements.txt       # Dependencies
├── setup.py              # Package setup
└── README.md             # This file
```

## Edit Guardrails

The tool implements strict guardrails to ensure edits are minimal:

### Automated Checks

| Check | Action if Failed |
|-------|------------------|
| Edit adds >50 words of new prose | Reject edit |
| Edit removes >20% of content | Reject edit |
| Source URL returns 404 | Flag source |
| Source on deprecated list | Reject source |
| Edit touches BLP article | Skip article entirely |
| Similarity ratio < 0.85 | Reject edit |

### Character-Level Diff Analysis

The system calculates similarity between original and edited text to ensure changes are truly minimal. Edits that change more than 15% of the text are rejected unless they're adding citations.

## Wikipedia Policy Compliance

All edits follow Wikipedia policies:

- **WP:CITE**: Proper citation format and templates
- **WP:MOS**: Manual of Style compliance
- **WP:NPOV**: Neutral point of view
- **WP:RS**: Reliable sources only
- **WP:BLP**: Skip living person biographies
- **WP:MINOR**: Mark edits as minor where appropriate

Edit summaries always include:
```
Copyedit: [description] (AI-assisted citation/cleanup, human-reviewed)
```

## Rate Limiting

To be a good Wikipedia citizen:
- Default: 10 edits per hour
- All edits require human approval
- Conflict detection before pushing
- Proper bot identification (if using bot account)

## Example Workflow

### Input Article (Before)

```wikitext
The Groveland Four were four African American men from Lake County, Florida
who were accused of raping a white woman in 1949. The case become a symbol
of racial injustice in the Jim Crow South.
```

### Proposed Edits

1. **[CITATION]** Add source for "accused of raping a white woman in 1949"
2. **[GRAMMAR]** Fix "become" → "became"
3. **[WIKILINK]** Link "Lake County, Florida"
4. **[WIKILINK]** Link "Jim Crow"
5. **[WIKILINK]** Link "African American"
6. **[FORMATTING]** Add category

### Output Article (After Review & Approval)

```wikitext
The '''Groveland Four''' were four [[African American]] men from
[[Lake County, Florida]] who were accused of raping a white woman in
1949.<ref>{{cite book |last=Green |first=Ben |title=Before His Time
|year=1999 |publisher=Free Press}}</ref> The case became a symbol of
racial injustice in the [[Jim Crow laws|Jim Crow South]].

[[Category:1949 in Florida]]
[[Category:Civil rights movement]]
```

## Safety & Ethics

### Design Principles

1. **Human in the Loop**: All edits require explicit human approval
2. **Transparency**: Edit summaries clearly indicate AI assistance
3. **Minimal Intervention**: Only fix what's broken, don't expand
4. **Source Verification**: Only cite claims already in the article
5. **Policy Compliance**: Follow all Wikipedia guidelines

### What We Skip

- Biographies of Living Persons (BLP) - too risky
- Protected/semi-protected pages
- Articles under active dispute
- Articles flagged for deletion

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## License

MIT License - see LICENSE file for details

## Acknowledgments

- Built with [Anthropic Claude](https://www.anthropic.com/claude)
- Uses [mwclient](https://github.com/mwclient/mwclient) for Wikipedia API
- Follows [Wikipedia policies](https://en.wikipedia.org/wiki/Wikipedia:Policies_and_guidelines)

## Support

- Report issues: [GitHub Issues](https://github.com/yourorg/wiki-cite/issues)
- Discussions: [GitHub Discussions](https://github.com/yourorg/wiki-cite/discussions)

## Disclaimer

This tool is designed to assist Wikipedia editors, not replace them. All edits must be reviewed by humans before submission. The tool follows Wikipedia's policies on bot-assisted editing and clearly labels all AI-assisted contributions.
