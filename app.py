"""
JTC Building Explorer - Flask backend with Azure OpenAI RAG and document generation.
"""
from flask import Flask, render_template, jsonify, request, send_file
import os
import re
import json
import requests
from pathlib import Path
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

# Land parcels data - matching documents in Azure AI Search index
LAND_PARCELS = [
    {"postal_code": "528872", "name": "Tampines North", "coordinates": [103.9456, 1.3750],
     "building_type": "Single-Use Factory", "land_area": "3.0 ha", "zoning": "B2"},
    {"postal_code": "738339", "name": "Woodlands North", "coordinates": [103.7867, 1.4520],
     "building_type": "Data Center", "land_area": "2.8 ha", "zoning": "B2"},
    {"postal_code": "729755", "name": "Sungei Kadut", "coordinates": [103.7543, 1.4180],
     "building_type": "Green Technology Hub", "land_area": "2.5 ha", "zoning": "B2"},
    {"postal_code": "637371", "name": "Tuas South", "coordinates": [103.6367, 1.2867],
     "building_type": "Single-Use Factory", "land_area": "4.5 ha", "zoning": "B2-Heavy"},
    {"postal_code": "508988", "name": "Loyang", "coordinates": [103.9750, 1.3650],
     "building_type": "Data Center", "land_area": "2.2 ha", "zoning": "B2"},
    {"postal_code": "628502", "name": "Pioneer", "coordinates": [103.6950, 1.3150],
     "building_type": "Green Technology Hub", "land_area": "3.5 ha", "zoning": "B2"},
    {"postal_code": "498793", "name": "Changi North", "coordinates": [103.9850, 1.3850],
     "building_type": "Data Center", "land_area": "2.0 ha", "zoning": "BP"},
    {"postal_code": "758069", "name": "Senoko", "coordinates": [103.8050, 1.4450],
     "building_type": "Single-Use Factory", "land_area": "3.0 ha", "zoning": "B2"},
    {"postal_code": "728710", "name": "Kranji", "coordinates": [103.7550, 1.4280],
     "building_type": "Green Technology Hub", "land_area": "2.8 ha", "zoning": "B1"},
]

# All parcels can be highlighted
HIGHLIGHT_POSTAL_CODES = [p["postal_code"] for p in LAND_PARCELS]

# ---- SLA Land Survey Districts ----
_sla_districts_cache = None

def load_sla_districts():
    """Load SLA Land Survey Districts from GeoJSON file."""
    global _sla_districts_cache
    if _sla_districts_cache is not None:
        return _sla_districts_cache
    
    geojson_path = Path(__file__).parent / "SLALandSurveyDistrict.geojson"
    try:
        with open(geojson_path, 'r', encoding='utf-8') as f:
            _sla_districts_cache = json.load(f)
        print(f"[OK] Loaded {len(_sla_districts_cache.get('features', []))} SLA survey districts")
        return _sla_districts_cache
    except Exception as e:
        print(f"[ERROR] Failed to load SLA districts: {e}")
        return None


def parse_district_info(description: str) -> dict:
    """Parse district information from HTML description in GeoJSON."""
    info = {
        "survey_district": None,
        "inc_crc": None,
        "update_date": None
    }
    
    # Extract SURVEY_DISTRICT (e.g., MK31, TS11)
    district_match = re.search(r'SURVEY_DISTRICT</th>\s*<td>([^<]+)', description)
    if district_match:
        info["survey_district"] = district_match.group(1).strip()
    
    # Extract INC_CRC
    crc_match = re.search(r'INC_CRC</th>\s*<td>([^<]+)', description)
    if crc_match:
        info["inc_crc"] = crc_match.group(1).strip()
    
    # Extract FMEL_UPD_D (update date)
    date_match = re.search(r'FMEL_UPD_D</th>\s*<td>([^<]+)', description)
    if date_match:
        info["update_date"] = date_match.group(1).strip()
    
    return info


def point_in_polygon(point: tuple, polygon: list) -> bool:
    """Check if a point (lon, lat) is inside a polygon using ray casting algorithm."""
    x, y = point
    n = len(polygon)
    inside = False
    
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    
    return inside


def find_district_for_point(lon: float, lat: float) -> dict:
    """Find which SLA survey district a point falls into."""
    districts = load_sla_districts()
    if not districts:
        return None
    
    point = (lon, lat)
    
    for feature in districts.get("features", []):
        geometry = feature.get("geometry", {})
        geom_type = geometry.get("type")
        
        if geom_type == "Polygon":
            # Single polygon - coordinates[0] is the outer ring
            coords = geometry.get("coordinates", [[]])[0]
            if point_in_polygon(point, coords):
                desc = feature.get("properties", {}).get("Description", "")
                return parse_district_info(desc)
        
        elif geom_type == "MultiPolygon":
            # Multiple polygons
            for polygon in geometry.get("coordinates", []):
                if polygon and point_in_polygon(point, polygon[0]):
                    desc = feature.get("properties", {}).get("Description", "")
                    return parse_district_info(desc)
    
    return None


def get_all_districts_summary() -> list:
    """Get a summary of all districts with their codes."""
    districts = load_sla_districts()
    if not districts:
        return []
    
    summary = []
    for feature in districts.get("features", []):
        desc = feature.get("properties", {}).get("Description", "")
        info = parse_district_info(desc)
        if info["survey_district"]:
            # Calculate centroid for the district
            geometry = feature.get("geometry", {})
            coords = geometry.get("coordinates", [[]])[0] if geometry.get("type") == "Polygon" else []
            if coords:
                centroid_lon = sum(c[0] for c in coords) / len(coords)
                centroid_lat = sum(c[1] for c in coords) / len(coords)
                info["centroid"] = [centroid_lon, centroid_lat]
            summary.append(info)
    
    return summary


# Keywords that trigger showing all highlighted buildings
TRIGGER_KEYWORDS = ["sample clauses", "single use factory", "single-use factory", "single used factory", "green technology", "data center", "data centre"]

# District code pattern (MK01-MK99, TS01-TS99)
DISTRICT_PATTERN = re.compile(r'\b(MK|TS|mk|ts)\s*(\d{1,2})\b')

# District aliases for natural language queries
DISTRICT_ALIASES = {
    # Central areas
    "downtown": ["TS01", "TS02", "TS03", "TS04", "TS05"],
    "central": ["TS01", "TS02", "TS03", "TS04", "TS05", "TS06"],
    "orchard": ["TS09", "TS10"],
    "marina": ["TS01", "TS02"],
    "cbd": ["TS01", "TS02", "TS03", "TS04"],
    "raffles": ["TS01"],
    "tanjong pagar": ["TS04", "TS05"],
    "chinatown": ["TS05", "TS06"],
    # East areas
    "tampines": ["MK31", "MK32"],
    "bedok": ["MK27", "MK28"],
    "changi": ["MK33", "MK34"],
    "pasir ris": ["MK30"],
    "east coast": ["MK26", "MK27"],
    "geylang": ["MK24", "MK25"],
    # West areas
    "jurong": ["MK04", "MK05", "MK06"],
    "tuas": ["MK01", "MK02"],
    "pioneer": ["MK03"],
    "clementi": ["MK08"],
    "bukit batok": ["MK09"],
    "bukit timah": ["MK10", "MK11"],
    # North areas
    "woodlands": ["MK17", "MK18"],
    "sembawang": ["MK19"],
    "yishun": ["MK16"],
    "sungei kadut": ["MK17"],
    "mandai": ["MK15"],
    # North-East areas
    "sengkang": ["MK21", "MK22"],
    "punggol": ["MK23"],
    "hougang": ["MK20"],
    "serangoon": ["MK20", "MK21"],
    "ang mo kio": ["MK14"],
    # Industrial areas
    "industrial": ["MK01", "MK02", "MK03", "MK04", "MK17"],
    "loyang": ["MK33"],
    "senoko": ["MK18"],
    "kranji": ["MK17"],
}


def find_districts_in_message(message: str) -> list:
    """Extract district codes from message using patterns and aliases."""
    msg_lower = message.lower()
    districts = set()
    
    # First, try to match explicit district codes (MK31, TS11, etc.)
    matches = DISTRICT_PATTERN.findall(message)
    for prefix, num in matches:
        code = f"{prefix.upper()}{int(num):02d}"
        districts.add(code)
    
    # Then check for location aliases
    for alias, codes in DISTRICT_ALIASES.items():
        if alias in msg_lower:
            districts.update(codes)
    
    return list(districts)


def get_district_feature_by_code(district_code: str) -> dict:
    """Get the GeoJSON feature for a specific district code."""
    districts = load_sla_districts()
    if not districts:
        return None
    
    for feature in districts.get("features", []):
        desc = feature.get("properties", {}).get("Description", "")
        info = parse_district_info(desc)
        if info["survey_district"] == district_code:
            return feature
    return None


# Semantic patterns for document generation requests
DOC_PATTERNS = [
    r"draft.*(?:tender|agreement|document|contract|docs|doc)",
    r"generate.*(?:tender|agreement|document|contract|docs|doc)",
    r"create.*(?:tender|agreement|document|contract|docs|doc)",
    r"prepare.*(?:tender|agreement|document|contract|docs|doc)",
    r"make.*(?:tender|agreement|document|contract|docs|doc)",
    r"write.*(?:tender|agreement|document|contract|docs|doc)",
    r"produce.*(?:tender|agreement|document|contract|docs|doc)",
    r"land.*sales.*(?:tender|agreement|document|contract|docs|doc|in|at|for)",
    r"sales.*tender",
    r"tender.*document",
    r"(?:tender|agreement|document|contract).*for.*(?:parcel|property|land|postal)",
    r"(?:i need|i want|can you|please).*(?:tender|agreement|document|contract)",
    r"(?:i plan|planning).*(?:land.*sales|tender|development)",
    r"draft.*for.*(?:factory|manufacturing|industrial|semiconductor)",
    r"docx|word.*(?:file|document)",
]

# Patterns to extract purpose/use from message
PURPOSE_PATTERNS = [
    r"for\s+(?:a\s+)?([\w\s]+?)\s+(?:factory|plant|facility|development)",
    r"single\s+use\s+([\w\s]+)",
    r"(?:purpose|use|used for)\s*:?\s*([\w\s]+)",
    r"(?:semiconductor|wafer|manufacturing|industrial)\s+([\w\s]+)",
]

# Location keywords mapped to parcels for semantic matching
LOCATION_ALIASES = {
    "528872": ["tampines", "tampines north"],
    "738339": ["woodlands", "woodlands north"],
    "729755": ["sungei kadut", "kadut"],
    "637371": ["tuas", "tuas south"],
    "508988": ["loyang"],
    "628502": ["pioneer"],
    "498793": ["changi", "changi north"],
    "758069": ["senoko"],
    "728710": ["kranji"],
}


def is_document_request(message: str) -> bool:
    """Check if message is requesting document generation."""
    msg_lower = message.lower()
    for pattern in DOC_PATTERNS:
        if re.search(pattern, msg_lower):
            return True
    return False


def extract_purpose(message: str) -> str:
    """Extract the intended purpose/use from the message."""
    msg_lower = message.lower()
    
    # Common purpose keywords
    purposes = []
    
    if "single use factory" in msg_lower or "single-use factory" in msg_lower:
        purposes.append("Single-Use Factory")
    if "semiconductor" in msg_lower:
        purposes.append("Semiconductor Manufacturing")
    if "wafer" in msg_lower:
        purposes.append("Wafer Fabrication")
    if "manufacturing" in msg_lower:
        purposes.append("Manufacturing")
    if "r&d" in msg_lower or "research" in msg_lower:
        purposes.append("R&D Facility")
    if "electronics" in msg_lower:
        purposes.append("Electronics Manufacturing")
    if "pharmaceutical" in msg_lower:
        purposes.append("Pharmaceutical Manufacturing")
    if "chemical" in msg_lower:
        purposes.append("Chemical Processing")
    
    # Try pattern matching
    for pattern in PURPOSE_PATTERNS:
        match = re.search(pattern, msg_lower)
        if match:
            extracted = match.group(1).strip()
            if extracted and len(extracted) > 2:
                purposes.append(extracted.title())
    
    return ", ".join(set(purposes)) if purposes else None


def find_parcel_from_message(message: str) -> dict:
    """Find parcel from message using postal code or location name."""
    msg_lower = message.lower()
    
    # First try postal code
    postal_match = re.search(r'\b(\d{6})\b', message)
    if postal_match:
        postal_code = postal_match.group(1)
        parcel = next((p for p in LAND_PARCELS if p["postal_code"] == postal_code), None)
        if parcel:
            return parcel
    
    # Then try location aliases
    for postal_code, aliases in LOCATION_ALIASES.items():
        for alias in aliases:
            if alias in msg_lower:
                parcel = next((p for p in LAND_PARCELS if p["postal_code"] == postal_code), None)
                if parcel:
                    return parcel
    
    # Try matching parcel names directly
    for parcel in LAND_PARCELS:
        name_parts = parcel["name"].lower().split()
        # Check if any significant word from parcel name is in message
        for part in name_parts:
            if len(part) > 3 and part in msg_lower:
                return parcel
    
    return None


def get_rag_response_with_url(parcel: dict, sas_url: str) -> str:
    """Send document URL to RAG chatbot for a formatted response."""
    if not openai_client:
        return f"Generated **Land Sales Tender Document** for **{parcel['name']}** ({parcel['postal_code']}).\n\n📥 [Download]({sas_url})\n\n*Link expires in 24 hours.*"
    
    try:
        prompt = f"""A Land Sales Tender Document has been generated for the following property:

Property: {parcel['name']}
Postal Code: {parcel['postal_code']}
Land Area: {parcel.get('land_area', 'N/A')}
Zoning: {parcel.get('zoning', 'N/A')}
Building Type: {parcel.get('building_type', 'N/A')}

Document Download URL: {sas_url}

Please provide a brief, professional response confirming the document has been generated. Include the download link and mention the link expires in 24 hours. Keep it concise."""

        response = openai_client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": "You are a helpful JTC assistant. Provide concise, professional responses."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.7
        )
        return response.choices[0].message.content
    except:
        return f"Generated **Land Sales Tender Document** for **{parcel['name']}** ({parcel['postal_code']}).\n\n📥 [Download]({sas_url})\n\n*Link expires in 24 hours.*"


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
    from document_generator import generate_land_sales_tender_document, upload_to_blob, generate_local
    return {"generate": generate_land_sales_tender_document, "upload": upload_to_blob, "local": generate_local}


# Routes
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/welcome')
def welcome():
    return jsonify({"message": "Welcome to JTC Building Explorer! Ask me about JTC properties or request a land sales agreement."})


# ---- SLA Survey Districts API Endpoints ----

@app.route('/api/sla-districts')
def sla_districts():
    """Return SLA Land Survey District boundaries as GeoJSON."""
    districts = load_sla_districts()
    if not districts:
        return jsonify({"error": "Failed to load SLA districts data"}), 500
    
    # Optionally filter by district code(s)
    district_code = request.args.get("district", "").upper()
    district_codes = request.args.get("districts", "").upper()  # comma-separated list
    
    # Parse multiple district codes
    codes_to_filter = []
    if district_codes:
        codes_to_filter = [c.strip() for c in district_codes.split(",") if c.strip()]
    elif district_code:
        codes_to_filter = [district_code]
    
    if codes_to_filter:
        filtered_features = []
        for f in districts.get("features", []):
            desc = f.get("properties", {}).get("Description", "")
            info = parse_district_info(desc)
            if info["survey_district"]:
                # Check if district matches any of the filter codes
                for code in codes_to_filter:
                    if code in info["survey_district"].upper():
                        filtered_features.append(f)
                        break
        return jsonify({
            "type": "FeatureCollection",
            "features": filtered_features,
            "count": len(filtered_features),
            "filtered_by": codes_to_filter
        })
    
    return jsonify({
        "type": "FeatureCollection",
        "features": districts.get("features", []),
        "count": len(districts.get("features", []))
    })


@app.route('/api/sla-districts/summary')
def sla_districts_summary():
    """Return a summary list of all SLA survey districts."""
    summary = get_all_districts_summary()
    return jsonify({
        "districts": summary,
        "count": len(summary)
    })


@app.route('/api/sla-districts/lookup')
def sla_district_lookup():
    """Find which survey district a coordinate falls into.
    
    Query params:
        lon: Longitude (required)
        lat: Latitude (required)
    
    Example: /api/sla-districts/lookup?lon=103.8198&lat=1.3521
    """
    try:
        lon = float(request.args.get("lon"))
        lat = float(request.args.get("lat"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid coordinates. Provide lon and lat as numbers."}), 400
    
    district_info = find_district_for_point(lon, lat)
    
    if district_info:
        return jsonify({
            "found": True,
            "coordinates": {"lon": lon, "lat": lat},
            "district": district_info
        })
    else:
        return jsonify({
            "found": False,
            "coordinates": {"lon": lon, "lat": lat},
            "message": "No district found for this location (may be outside Singapore or in water)"
        })


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
    """Return land parcels as GeoJSON with survey district info."""
    filter_codes = request.args.get("postal_codes", "").split(",")
    filter_codes = [c.strip() for c in filter_codes if c.strip()]
    include_district = request.args.get("include_district", "true").lower() == "true"
    
    features = []
    for p in LAND_PARCELS:
        if filter_codes and p["postal_code"] not in filter_codes:
            continue
        
        props = {k: v for k, v in p.items() if k != "coordinates"}
        
        # Look up survey district for this parcel
        if include_district:
            lon, lat = p["coordinates"]
            district_info = find_district_for_point(lon, lat)
            if district_info:
                props["survey_district"] = district_info["survey_district"]
        
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": p["coordinates"]},
            "properties": props
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
    is_doc_request = is_document_request(message)
    is_trigger = any(kw in msg_lower for kw in TRIGGER_KEYWORDS)
    
    # Document generation flow
    if is_doc_request:
        # Find parcel by postal code OR location name
        parcel = find_parcel_from_message(message)
        
        # Extract purpose from message
        purpose = extract_purpose(message)
        
        if parcel:
            postal_code = parcel["postal_code"]
            try:
                doc_gen = get_document_generator()
                result = doc_gen["upload"](postal_code, "Sample Buyer Pte Ltd", purpose)
                sas_url = result['sas_url']
                
                # Send to RAG for a nicely formatted response with the URL
                rag_response = get_rag_response_with_url(parcel, sas_url)
                
                return jsonify({
                    "response": rag_response,
                    "action": {"type": "document_generated", "document_url": sas_url, "postal_code": postal_code}
                })
            except ValueError:
                filepath = doc_gen["local"](postal_code, "Sample Buyer Pte Ltd", purpose)
                return jsonify({"response": f"Generated agreement saved to `{filepath}`.\n\n⚠️ Configure Azure Blob Storage for shareable links.", "action": {"type": "document_generated_local", "filepath": filepath}})
        
        # No parcel found - list available options
        table = "\n".join([f"| {p['postal_code']} | {p['name']} |" for p in LAND_PARCELS])
        return jsonify({"response": f"I couldn't identify the location. Please specify:\n\n| Code | Location |\n|------|----------|\n{table}\n\nYou can say things like 'draft agreement for Tampines North' or 'create land sales doc for 528765'."})
    
    # Regular chat with RAG
    return chat_rag(message, is_trigger)


@app.route('/api/chat', methods=['POST'])
def chat():
    """RAG chat endpoint."""
    data = request.get_json()
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "No message"}), 400
    
    is_trigger = any(kw in message.lower() for kw in TRIGGER_KEYWORDS)
    return chat_rag(message, is_trigger)


def chat_rag(message, is_trigger=False):
    """Process chat with Azure OpenAI RAG."""
    if not openai_client:
        return jsonify({"error": "Azure OpenAI not configured"}), 503
    
    # Check for district-related queries FIRST
    msg_lower = message.lower()
    district_keywords = ["district", "mukim", "survey", "mk ", "ts ", "area", "zone", "region", "location"]
    is_district_query = any(kw in msg_lower for kw in district_keywords) or DISTRICT_PATTERN.search(message)
    
    # Find any mentioned districts
    mentioned_districts = find_districts_in_message(message)
    
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
        
        # Get district summary for context
        district_summary = get_all_districts_summary()
        district_codes = [d["survey_district"] for d in district_summary if d.get("survey_district")]
        
        system_prompt = f"""You are a JTC Building Explorer assistant. Help users with JTC industrial properties in Singapore.

Available building types: {', '.join(types) if types else 'none'}

Singapore is divided into Survey Districts:
- MK (Mukim) districts: Rural/suburban areas (MK01-MK34+)
- TS (Town Subdivision) districts: Urban/central areas (TS01-TS30+)

Available district codes: {', '.join(district_codes[:30])}... (total: {len(district_codes)})

You can control the map with ACTION commands at end of response:
- ACTION:SHOW_ALL - Show all buildings
- ACTION:FILTER:<type> - Filter by building type
- ACTION:POSTAL:<code1>,<code2> - Highlight postal codes
- ACTION:DISTRICT:<code1>,<code2> - Highlight survey districts (e.g., ACTION:DISTRICT:MK31,MK32)
- ACTION:CLEAR - Clear map

When users ask about districts or areas, include the ACTION:DISTRICT command to highlight them on the map.

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
                elif cmd.startswith('DISTRICT:'):
                    codes = [c.strip().upper() for c in cmd[9:].split(',') if c.strip()]
                    action = {"type": "highlight_districts", "district_codes": codes}
            else:
                clean_lines.append(line)
        
        text = '\n'.join(clean_lines).strip()
        
        # If districts were mentioned but no action was generated, add highlight action
        if mentioned_districts and not action:
            action = {"type": "highlight_districts", "district_codes": mentioned_districts}
            if "highlighted" not in text.lower() and "map" not in text.lower():
                text += f"\n\n📍 **Districts highlighted on map: {', '.join(mentioned_districts)}**"
        
        # Override for trigger queries (sample clauses, single use factory, etc.)
        if is_trigger:
            action = {"type": "highlight_postal_codes", "postal_codes": HIGHLIGHT_POSTAL_CODES}
            if "parcels" not in text.lower():
                text += "\n\n**Relevant land parcels highlighted on map.**"
        
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
