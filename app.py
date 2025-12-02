"""
JTC Building Explorer - Flask backend with Azure OpenAI RAG and document generation.
"""
from flask import Flask, render_template, jsonify, request, send_file
import os
import re
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()

app = Flask(__name__)

# Azure Configuration
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX")
AZURE_SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY")

# Initialize Azure OpenAI
openai_client = None
if AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY:
    openai_client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION
    )
    print(f"[OK] Azure OpenAI: {AZURE_OPENAI_ENDPOINT}")
    if AZURE_SEARCH_ENDPOINT:
        print(f"[OK] Azure AI Search: {AZURE_SEARCH_INDEX}")

# Data.gov.sg API
DATAGOV_BASE = "https://api-open.data.gov.sg/v1/public/api"
JTC_BUILDING_DATASET = "d_db12567505086c5319ba8b95ff195ba2"

# Cache
_jtc_cache = {"data": None, "expires": None}

# Land parcels data
LAND_PARCELS = [
    {"postal_code": "528765", "name": "Tampines North Industrial", "coordinates": [103.9456, 1.3712],
     "chemical_allowed": True, "chemical_type": "Class B2", "land_area": "2.5 ha", "zoning": "B2", "premium": "SGD 45M"},
    {"postal_code": "637141", "name": "Jurong CleanTech Park", "coordinates": [103.6869, 1.3418],
     "chemical_allowed": True, "chemical_type": "Green chemistry", "land_area": "1.8 ha", "zoning": "BP-White", "premium": "SGD 38M"},
    {"postal_code": "757743", "name": "Woodlands Industrial Park", "coordinates": [103.7867, 1.4382],
     "chemical_allowed": True, "chemical_type": "Petrochemical", "land_area": "3.2 ha", "zoning": "B2", "premium": "SGD 28M"},
    {"postal_code": "797564", "name": "Seletar Aerospace Park", "coordinates": [103.8667, 1.4167],
     "chemical_allowed": True, "chemical_type": "Aviation fuel", "land_area": "2.8 ha", "zoning": "B2-Aero", "premium": "SGD 42M"},
    {"postal_code": "637371", "name": "Tuas South Industrial", "coordinates": [103.6367, 1.2867],
     "chemical_allowed": True, "chemical_type": "Full spectrum", "land_area": "4.5 ha", "zoning": "B2-Heavy", "premium": "SGD 35M"},
    {"postal_code": "569938", "name": "Ang Mo Kio Industrial Park 2", "coordinates": [103.8567, 1.3717],
     "chemical_allowed": True, "chemical_type": "Pharmaceutical", "land_area": "1.5 ha", "zoning": "B1-White", "premium": "SGD 48M"},
    {"postal_code": "417943", "name": "Kaki Bukit Industrial", "coordinates": [103.9067, 1.3367],
     "chemical_allowed": True, "chemical_type": "Industrial solvents", "land_area": "1.0 ha", "zoning": "B2", "premium": "SGD 18M"},
    {"postal_code": "486015", "name": "Changi Business Park", "coordinates": [103.9633, 1.3342],
     "chemical_allowed": False, "chemical_type": "Not permitted", "land_area": "1.2 ha", "zoning": "BP", "premium": "SGD 52M"},
    {"postal_code": "409015", "name": "Paya Lebar Industrial", "coordinates": [103.8917, 1.3217],
     "chemical_allowed": False, "chemical_type": "Non-hazardous only", "land_area": "0.8 ha", "zoning": "B1", "premium": "SGD 22M"},
]

CHEMICAL_POSTAL_CODES = [p["postal_code"] for p in LAND_PARCELS if p["chemical_allowed"]]
CHEMICAL_KEYWORDS = ["chemical", "chemicals", "processing", "hazardous", "petrochemical", "pharmaceutical", "solvent"]
DOC_KEYWORDS = ["draft", "generate", "create", "agreement", "document", "contract", "docx", "word"]


def get_jtc_buildings():
    """Fetch JTC buildings from Data.gov.sg with caching."""
    global _jtc_cache
    now = datetime.now(timezone.utc)
    
    if _jtc_cache["data"] and _jtc_cache["expires"] and now < _jtc_cache["expires"]:
        return _jtc_cache["data"]
    
    try:
        resp = requests.get(f"{DATAGOV_BASE}/datasets/{JTC_BUILDING_DATASET}/initiate-download", timeout=15)
        result = resp.json()
        if result.get("code") != 0:
            return None
        
        download_url = result.get("data", {}).get("url")
        if not download_url:
            return None
        
        geojson = requests.get(download_url, timeout=60).json()
        _jtc_cache = {"data": geojson, "expires": now + timedelta(hours=1)}
        return geojson
    except Exception as e:
        print(f"JTC API error: {e}")
        return None


def get_document_generator():
    """Import document generator module."""
    from document_generator import generate_land_sales_agreement, upload_to_blob, generate_local
    return {"generate": generate_land_sales_agreement, "upload": upload_to_blob, "local": generate_local}


# Routes
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/welcome')
def welcome():
    return jsonify({"message": "Welcome to JTC Building Explorer! Ask me about JTC properties or request a land sales agreement."})


@app.route('/api/jtc-buildings')
def jtc_buildings():
    """Return JTC building footprints."""
    geojson = get_jtc_buildings()
    if not geojson:
        return jsonify({"error": "Failed to fetch data"}), 502
    
    building_type = request.args.get("building_type", "").lower()
    features = geojson.get("features", [])
    
    if building_type:
        filtered = []
        for f in features:
            desc = f.get("properties", {}).get("Description", "")
            match = re.search(r'JTC_BUILDING_TYPE</th>\s*<td>([^<]+)', desc)
            if match and building_type in match.group(1).lower():
                filtered.append(f)
        features = filtered
    
    return jsonify({"type": "FeatureCollection", "features": features, "count": len(features)})


@app.route('/api/jtc-building-types')
def jtc_building_types():
    """Get available building types."""
    geojson = get_jtc_buildings()
    if not geojson:
        return jsonify({"error": "Failed to fetch data"}), 502
    
    types = set()
    for f in geojson.get("features", []):
        desc = f.get("properties", {}).get("Description", "")
        match = re.search(r'JTC_BUILDING_TYPE</th>\s*<td>([^<]+)', desc)
        if match:
            types.add(match.group(1).strip())
    
    return jsonify({"building_types": sorted(types), "total": len(geojson.get("features", []))})


@app.route('/api/demo-land-parcels')
def demo_land_parcels():
    """Return land parcels as GeoJSON."""
    filter_codes = request.args.get("postal_codes", "").split(",")
    filter_codes = [c.strip() for c in filter_codes if c.strip()]
    
    features = []
    for p in LAND_PARCELS:
        if filter_codes and p["postal_code"] not in filter_codes:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": p["coordinates"]},
            "properties": {k: v for k, v in p.items() if k != "coordinates"}
        })
    
    return jsonify({"type": "FeatureCollection", "features": features})


@app.route('/api/generate-agreement', methods=['POST'])
def generate_agreement():
    """Generate a land sales agreement document."""
    data = request.get_json()
    postal_code = data.get("postal_code")
    buyer_name = data.get("buyer_name", "Sample Buyer Pte Ltd")
    
    if not postal_code:
        return jsonify({"error": "postal_code required"}), 400
    
    parcel = next((p for p in LAND_PARCELS if p["postal_code"] == postal_code), None)
    if not parcel:
        return jsonify({"error": f"Parcel {postal_code} not found"}), 404
    
    try:
        doc_gen = get_document_generator()
        try:
            result = doc_gen["upload"](postal_code, buyer_name)
            return jsonify({"success": True, "document": result})
        except ValueError:
            filepath = doc_gen["local"](postal_code, buyer_name)
            return jsonify({"success": True, "document": {"filepath": filepath, "note": "Blob storage not configured"}})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/chat-with-tools', methods=['POST'])
def chat_with_tools():
    """Chat endpoint with document generation support."""
    if not openai_client:
        return jsonify({"error": "Azure OpenAI not configured"}), 503
    
    data = request.get_json()
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "No message"}), 400
    
    msg_lower = message.lower()
    is_doc_request = any(kw in msg_lower for kw in DOC_KEYWORDS) and "agreement" in msg_lower
    is_chemical = any(kw in msg_lower for kw in CHEMICAL_KEYWORDS)
    
    # Document generation flow
    if is_doc_request:
        postal_match = re.search(r'\b(\d{6})\b', message)
        postal_code = postal_match.group(1) if postal_match else None
        
        if postal_code:
            parcel = next((p for p in LAND_PARCELS if p["postal_code"] == postal_code), None)
            if parcel:
                try:
                    doc_gen = get_document_generator()
                    result = doc_gen["upload"](postal_code)
                    response = f"Generated **Land Sales Agreement** for **{parcel['name']}** ({postal_code}).\n\n📥 [Download]({result['sas_url']})\n\n*Link expires in 24 hours.*"
                    return jsonify({"response": response, "action": {"type": "document_generated", "document_url": result["sas_url"], "postal_code": postal_code}})
                except ValueError:
                    filepath = doc_gen["local"](postal_code)
                    return jsonify({"response": f"Generated agreement saved to `{filepath}`.\n\n⚠️ Configure Azure Blob Storage for shareable links.", "action": {"type": "document_generated_local", "filepath": filepath}})
        
        # List available parcels
        table = "\n".join([f"| {p['postal_code']} | {p['name']} |" for p in LAND_PARCELS])
        return jsonify({"response": f"Specify a postal code:\n\n| Code | Location |\n|------|----------|\n{table}"})
    
    # Regular chat with RAG
    return chat_rag(message, is_chemical)


@app.route('/api/chat', methods=['POST'])
def chat():
    """RAG chat endpoint."""
    data = request.get_json()
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "No message"}), 400
    
    is_chemical = any(kw in message.lower() for kw in CHEMICAL_KEYWORDS)
    return chat_rag(message, is_chemical)


def chat_rag(message, is_chemical=False):
    """Process chat with Azure OpenAI RAG."""
    if not openai_client:
        return jsonify({"error": "Azure OpenAI not configured"}), 503
    
    try:
        # Get building types for context
        geojson = get_jtc_buildings()
        types = []
        if geojson:
            for f in geojson.get("features", []):
                match = re.search(r'JTC_BUILDING_TYPE</th>\s*<td>([^<]+)', f.get("properties", {}).get("Description", ""))
                if match:
                    types.append(match.group(1).strip().lower())
            types = sorted(set(types))
        
        system_prompt = f"""You are a JTC Building Explorer assistant. Help users with JTC industrial properties in Singapore.

Available building types: {', '.join(types) if types else 'none'}

You can control the map with ACTION commands at end of response:
- ACTION:SHOW_ALL - Show all buildings
- ACTION:FILTER:<type> - Filter by building type
- ACTION:POSTAL:<code1>,<code2> - Highlight postal codes
- ACTION:CLEAR - Clear map

Keep responses concise and professional."""

        extra_body = None
        if AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_INDEX:
            extra_body = {
                "data_sources": [{
                    "type": "azure_search",
                    "parameters": {
                        "endpoint": AZURE_SEARCH_ENDPOINT,
                        "index_name": AZURE_SEARCH_INDEX,
                        "authentication": {"type": "api_key", "key": AZURE_SEARCH_KEY} if AZURE_SEARCH_KEY else {"type": "system_assigned_managed_identity"},
                        "query_type": "vector_simple_hybrid",
                        "embedding_dependency": {"type": "deployment_name", "deployment_name": "text-embedding-ada-002"},
                        "fields_mapping": {"content_fields": ["chunk"], "title_field": "title", "vector_fields": ["text_vector"]},
                        "top_n_documents": 10, "in_scope": False, "strictness": 1
                    }
                }]
            }
        
        response = openai_client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": message}],
            max_tokens=2000, temperature=0.7, extra_body=extra_body
        )
        
        text = response.choices[0].message.content
        action = None
        
        # Parse actions
        lines = text.strip().split('\n')
        clean_lines = []
        for line in lines:
            if line.strip().startswith('ACTION:'):
                cmd = line.strip()[7:]
                if cmd == 'SHOW_ALL':
                    action = {"type": "show_buildings"}
                elif cmd == 'CLEAR':
                    action = {"type": "clear_map"}
                elif cmd.startswith('FILTER:'):
                    action = {"type": "show_buildings", "filter": cmd[7:].lower()}
                elif cmd.startswith('POSTAL:'):
                    codes = [c.strip() for c in cmd[7:].split(',') if c.strip()]
                    action = {"type": "highlight_postal_codes", "postal_codes": codes}
            else:
                clean_lines.append(line)
        
        text = '\n'.join(clean_lines).strip()
        
        # Override for chemical queries
        if is_chemical:
            action = {"type": "highlight_postal_codes", "postal_codes": CHEMICAL_POSTAL_CODES}
            if "528765" not in text:
                text += "\n\n**Chemical-approved parcels:** " + ", ".join(CHEMICAL_POSTAL_CODES)
        
        result = {"response": text}
        if action:
            result["action"] = action
        
        return jsonify(result)
    
    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    print("Server: http://127.0.0.1:5000")
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)
