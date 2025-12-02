"""
JTC Building Explorer
Flask backend with JTC Data.gov.sg API integration and Azure OpenAI with On Your Data.
"""
from flask import Flask, render_template, jsonify, request
import os
import json
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from typing import Optional, Dict, List
from openai import AzureOpenAI

load_dotenv()

app = Flask(__name__)

# Azure OpenAI Configuration
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

# Azure AI Search Configuration (On Your Data)
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX")
AZURE_SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY")

# Initialize Azure OpenAI client
openai_client = None
if AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY:
    openai_client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION
    )
    print(f"[OK] Connected to Azure OpenAI at {AZURE_OPENAI_ENDPOINT}")
    if AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_INDEX:
        print(f"[OK] Azure AI Search configured: {AZURE_SEARCH_INDEX}")
else:
    print("[WARN] Azure OpenAI not configured - chat will be unavailable")

# API base URLs
DATAGOV_BASE = "https://api-open.data.gov.sg/v1/public/api"

# JTC Building Dataset IDs from Data.gov.sg
JTC_BUILDING_GEOJSON_ID = "d_db12567505086c5319ba8b95ff195ba2"
JTC_BUILDING_KML_ID = "d_431811cf528134ba1d28190f09735930"

# Cache for JTC Building data (refreshed periodically)
_jtc_cache = {"data": None, "expires": None}


# ---------------------- JTC Data.gov.sg API ----------------------

def _get_jtc_buildings() -> Optional[Dict]:
    """Fetch JTC Building GeoJSON from Data.gov.sg API with caching."""
    global _jtc_cache
    
    now = datetime.now(timezone.utc)
    
    # Return cached data if still valid (cache for 1 hour)
    if _jtc_cache["data"] and _jtc_cache["expires"] and now < _jtc_cache["expires"]:
        return _jtc_cache["data"]
    
    try:
        # Step 1: Initiate download to get pre-signed URL
        initiate_url = f"{DATAGOV_BASE}/datasets/{JTC_BUILDING_GEOJSON_ID}/initiate-download"
        resp = requests.get(initiate_url, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        
        if result.get("code") != 0:
            print(f"JTC API initiate error: {result.get('errorMsg')}")
            return None
        
        download_url = result.get("data", {}).get("url")
        if not download_url:
            print("No download URL returned from Data.gov.sg")
            return None
        
        # Step 2: Download the actual GeoJSON file
        geojson_resp = requests.get(download_url, timeout=60)
        geojson_resp.raise_for_status()
        geojson_data = geojson_resp.json()
        
        # Cache the result
        _jtc_cache["data"] = geojson_data
        _jtc_cache["expires"] = now + timedelta(hours=1)
        
        return geojson_data
    
    except Exception as e:
        print(f"JTC Building fetch error: {e}")
        return None


# ---------------------- Layer Registry ----------------------

def get_layer_registry() -> Dict[str, Dict]:
    """Dynamic registry of available map layers."""
    return {
        "jtc_buildings": {
            "title": "JTC Buildings",
            "description": "JTC industrial buildings footprints (factories, warehouses, etc.)",
            "synonyms": ["jtc", "building", "factory", "industrial", "warehouse", "terrace"],
            "source": "Data.gov.sg",
        },
    }


# ---------------------- Routes ----------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/health')
def health():
    """Health check endpoint."""
    # Test JTC API connectivity
    jtc_available = False
    try:
        test_url = f"{DATAGOV_BASE}/datasets/{JTC_BUILDING_GEOJSON_ID}/metadata"
        resp = requests.get(test_url, timeout=5)
        jtc_available = resp.status_code == 200
    except:
        pass
    
    return jsonify({
        "status": "ok",
        "jtc_api_available": jtc_available,
        "layers": list(get_layer_registry().keys())
    })


@app.route('/api/layers')
def layers_info():
    """Return available layers metadata."""
    return jsonify(get_layer_registry())


@app.route('/api/welcome')
def welcome():
    """Return welcome message for the chat assistant."""
    return jsonify({
        "message": "Welcome to JTC Building Explorer! I can help you explore JTC's industrial properties across Singapore and answer questions about land parcels. Try:\n\n• 'Show all buildings'\n• 'Tell me about Ang Mo Kio'\n• 'What building types are available?'"
    })


# ---------------------- JTC Buildings ----------------------

@app.route('/api/jtc-buildings')
def jtc_buildings():
    """
    Fetch JTC Building footprints from Data.gov.sg.
    Query params:
      - building_type: filter by JTC_BUILDING_TYPE (optional)
      - status: filter by BUILDING_STATUS (optional)
    """
    try:
        geojson = _get_jtc_buildings()
        
        if not geojson:
            return jsonify({"error": "Failed to fetch JTC Building data"}), 502
        
        # Optional filters
        building_type = request.args.get("building_type", "").lower()
        status = request.args.get("status", "").lower()
        
        features = geojson.get("features", [])
        
        # Apply filters if provided
        if building_type or status:
            filtered = []
            for f in features:
                props = f.get("properties", {})
                # Handle different property name formats (Name/Description from KML conversion)
                desc = props.get("Description", "")
                
                # Parse description HTML to extract attributes
                bldg_type = ""
                bldg_status = ""
                if "JTC_BUILDING_TYPE" in desc:
                    import re
                    type_match = re.search(r'JTC_BUILDING_TYPE</th>\s*<td>([^<]+)', desc)
                    if type_match:
                        bldg_type = type_match.group(1).lower()
                    status_match = re.search(r'BUILDING_STATUS</th>\s*<td>([^<]+)', desc)
                    if status_match:
                        bldg_status = status_match.group(1).lower()
                
                if building_type and building_type not in bldg_type:
                    continue
                if status and status not in bldg_status:
                    continue
                filtered.append(f)
            features = filtered
        
        return jsonify({
            "type": "FeatureCollection",
            "features": features,
            "metadata": {
                "count": len(features),
                "source": "Data.gov.sg - JTC Building",
                "dataset_id": JTC_BUILDING_GEOJSON_ID
            }
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/jtc-building-types')
def jtc_building_types():
    """Get distinct building types from JTC data."""
    try:
        geojson = _get_jtc_buildings()
        
        if not geojson:
            return jsonify({"error": "Failed to fetch JTC Building data"}), 502
        
        import re
        types = set()
        statuses = set()
        
        for f in geojson.get("features", []):
            desc = f.get("properties", {}).get("Description", "")
            
            type_match = re.search(r'JTC_BUILDING_TYPE</th>\s*<td>([^<]+)', desc)
            if type_match:
                types.add(type_match.group(1).strip())
            
            status_match = re.search(r'BUILDING_STATUS</th>\s*<td>([^<]+)', desc)
            if status_match:
                statuses.add(status_match.group(1).strip())
        
        return jsonify({
            "building_types": sorted(list(types)),
            "building_statuses": sorted(list(statuses)),
            "total_buildings": len(geojson.get("features", []))
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------- RAG Chat Endpoint ----------------------

@app.route('/api/chat', methods=['POST'])
def chat():
    """
    RAG-powered chat endpoint using Azure OpenAI with On Your Data (Azure AI Search).
    Retrieves context from Azure AI Search and answers questions.
    Returns both response text and optional map actions.
    """
    if not openai_client:
        return jsonify({"error": "Azure OpenAI not configured. Please set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY."}), 503
    
    try:
        data = request.get_json()
        user_message = data.get("message", "").strip()
        
        if not user_message:
            return jsonify({"error": "No message provided"}), 400
        
        # Get available building types for map filter actions
        geojson = _get_jtc_buildings()
        available_types = []
        if geojson:
            import re
            types_set = set()
            for f in geojson.get("features", []):
                desc = f.get("properties", {}).get("Description", "")
                type_match = re.search(r'JTC_BUILDING_TYPE</th>\s*<td>([^<]+)', desc)
                if type_match:
                    types_set.add(type_match.group(1).strip().lower())
            available_types = sorted(list(types_set))
        
        # System prompt for the assistant
        system_prompt = f"""You are an expert JTC Building Explorer assistant and agreement drafting specialist. You have access to JTC documentation and data through Azure AI Search.

Your role is to help users understand JTC's industrial properties in Singapore and draft professional agreements based on provided data sources.

Available map filters: {', '.join(available_types) if available_types else 'none'}

# AGREEMENT DRAFTING INSTRUCTIONS

Draft agreements in PDF or Word format based on provided data sources while referencing relevant details from the data.

When working with data sources, answers should first address any specific questions directly while providing clear reasoning. When drafting agreements, ensure the content aligns with the reference data, maintains professional formatting, and includes all necessary sections relevant to the agreement type highlighted by the user.

## Steps

1. **Understand the Query**:
   - If the query involves responding to a question, prioritize answering the question clearly and concisely.
   - If the query requests an agreement, confirm the type of agreement, necessary parties involved, and key terms referenced from the provided data source.

2. **Reference the Data Source**:
   - Interpret and extract key points from the source document(s) or provided input.
   - Ensure the response or drafted agreement incorporates all relevant data accurately.

3. **Draft the Agreement**:
   - Clearly format the agreement, starting with a title, introduction, and purpose.
   - Include appropriate sections such as:
     - Parties involved.
     - Recitals (if necessary).
     - Definitions.
     - Terms or obligations of the agreement.
     - Confidentiality/Termination clauses (if relevant).
     - Signatory areas.
   - Tailor the language to suit the level of formality expected and ensure clarity and professionalism.

4. **Prepare in Desired Format**:
   - If a specific format (e.g., PDF or Word document) is requested, format the content accordingly.

## Output Format

- Questions: Provide responses in text format directly based on the question and reference data, with any reasoning or explanations given before conclusions.
- Agreements: Provide the agreement content formatted professionally as raw text in the response. Clearly indicate sections or headings.

## Agreement Sections to Include

When drafting Land Sales Agreements or similar documents, include:
1. **Title**: Agreement name and type
2. **Parties**: Full names and details of all parties involved
3. **Recitals**: Background and purpose of the agreement
4. **Definitions**: Key terms used in the agreement
5. **Property Description**: Details of the land/property
6. **Purchase Price and Payment Terms**: Financial terms
7. **Conditions Precedent**: Requirements before completion
8. **Representations and Warranties**: Guarantees from parties
9. **Covenants**: Ongoing obligations
10. **Completion and Handover**: Transfer details
11. **Default and Remedies**: Consequences of breach
12. **Governing Law**: Jurisdiction and applicable law
13. **Signatures**: Signatory blocks for all parties

# MAP CONTROL INSTRUCTIONS

You can CONTROL THE MAP by including an ACTION line at the END of your response:
- Show all buildings: ACTION:SHOW_ALL
- Filter by type: ACTION:FILTER:<type> (e.g., ACTION:FILTER:terrace factory)
- Clear map: ACTION:CLEAR

When to trigger map actions:
- User says "show", "display", "load" → use appropriate ACTION
- User asks analytical questions → just answer, no ACTION
- User says "clear", "hide", "reset" → use ACTION:CLEAR

Keep responses professional, accurate, and helpful."""

        # Build the request with Azure AI Search data source
        extra_body = None
        if AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_INDEX:
            extra_body = {
                "data_sources": [
                    {
                        "type": "azure_search",
                        "parameters": {
                            "endpoint": AZURE_SEARCH_ENDPOINT,
                            "index_name": AZURE_SEARCH_INDEX,
                            "authentication": {
                                "type": "api_key",
                                "key": AZURE_SEARCH_KEY
                            } if AZURE_SEARCH_KEY else {
                                "type": "system_assigned_managed_identity"
                            },
                            "query_type": "vector_simple_hybrid",
                            "embedding_dependency": {
                                "type": "deployment_name",
                                "deployment_name": "text-embedding-ada-002"
                            },
                            "fields_mapping": {
                                "content_fields": ["chunk"],
                                "title_field": "title",
                                "vector_fields": ["text_vector"]
                            },
                            "top_n_documents": 10,
                            "in_scope": False,
                            "strictness": 1
                        }
                    }
                ]
            }
        
        # Call Azure OpenAI with On Your Data
        response = openai_client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            max_tokens=4000,
            temperature=0.7,
            extra_body=extra_body
        )
        
        assistant_message = response.choices[0].message.content
        
        # Parse action from response
        action = None
        response_text = assistant_message
        
        # Check for ACTION line at the end
        lines = assistant_message.strip().split('\n')
        for i, line in enumerate(lines):
            if line.strip().startswith('ACTION:'):
                action_str = line.strip()[7:]  # Remove "ACTION:"
                
                if action_str == 'SHOW_ALL':
                    action = {"type": "show_buildings", "filter": None}
                elif action_str == 'CLEAR':
                    action = {"type": "clear_map"}
                elif action_str.startswith('FILTER:'):
                    filter_type = action_str[7:].strip().lower()
                    action = {"type": "show_buildings", "filter": filter_type}
                
                # Remove action line from response text
                lines.pop(i)
                response_text = '\n'.join(lines).strip()
                break
        
        result = {
            "response": response_text,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens
            }
        }
        
        if action:
            result["action"] = action
        
        return jsonify(result)
    
    except Exception as e:
        import traceback
        print(f"Chat error: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


# ---------------------- Main ----------------------

if __name__ == '__main__':
    import logging
    import sys
    
    # Configure logging
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.INFO)
    
    print("Starting JTC Building Explorer...")
    print(f"Server will run at http://127.0.0.1:5000")
    
    try:
        app.run(host='127.0.0.1', debug=False, port=5000, use_reloader=False, threaded=True)
    except Exception as e:
        print(f"[ERROR] Server error: {e}")
        sys.exit(1)
