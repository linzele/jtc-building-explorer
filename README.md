# JTC Industrial Buildings Map

Interactive Singapore map for JTC industrial buildings exploration with AI-powered chat assistant.

## Features

- **JTC Buildings Layer** – Real-time GeoJSON data from Data.gov.sg with building type filtering
- **AI Chat Assistant** – Azure OpenAI-powered assistant with RAG for agreement drafting guidance
- **Interactive Map** – Leaflet.js with OneMap basemap, building popups with detailed information
- **Building Type Filtering** – Filter by Business Park, Flatted Factory, Specialized Park, etc.

## Tech Stack

- **Backend**: Flask (Python)
- **Frontend**: Leaflet.js, Bootstrap 5
- **AI**: Azure OpenAI + Azure AI Search (RAG)
- **Data Source**: JTC Data.gov.sg API

## Setup

### 1. Create Virtual Environment

```bash
cd "JTC Project"
python -m venv .venv

# Windows
.\.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

**Required:**
- `AZURE_OPENAI_ENDPOINT` – Your Azure OpenAI resource endpoint
- `AZURE_OPENAI_KEY` – Your Azure OpenAI API key
- `AZURE_OPENAI_DEPLOYMENT` – Deployment name (e.g., `gpt-4o`)
- `AZURE_SEARCH_ENDPOINT` – Azure AI Search endpoint
- `AZURE_SEARCH_KEY` – Azure AI Search API key
- `AZURE_SEARCH_INDEX` – Index name for RAG

### 4. Run Locally

```bash
python app.py
```

Open http://127.0.0.1:5000 in your browser.

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Main map interface |
| `GET /api/jtc-building-types` | JTC building GeoJSON data |
| `GET /api/welcome` | Welcome message with chat instructions |
| `POST /api/chat` | AI chat endpoint (RAG-enabled) |

## Map Controls

- **Layer Toggle**: Enable/disable JTC Buildings layer
- **Building Type Filter**: Dropdown to filter by building category
- **Click Buildings**: View detailed popup with building info
- **Chat Actions**: AI can control map via SHOW_ALL, FILTER, CLEAR commands

## Deployment

### Azure App Service

1. Set environment variables in App Service Configuration
2. Ensure `Procfile` is present for gunicorn
3. Push to GitHub and configure deployment

### Procfile

```
web: gunicorn app:app
```

## Data Sources

- **JTC Buildings**: [Data.gov.sg - JTC Building GeoJSON](https://data.gov.sg/datasets/d_db12567505086c5319ba8b95ff195ba2/view)
- **Basemap**: OneMap Grey Tiles

## License

MIT
