"""Document generator for JTC land sales agreements."""
import os
import io
from datetime import datetime, timedelta
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from dotenv import load_dotenv

load_dotenv()

STORAGE_CONN_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
STORAGE_CONTAINER = os.getenv("AZURE_STORAGE_CONTAINER", "land-sales-documents")


def generate_land_sales_agreement(parcel: dict, buyer: str = "Sample Buyer Pte Ltd") -> io.BytesIO:
    """Generate a Word document for land sales agreement."""
    doc = Document()
    
    # Title
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("LAND SALES AGREEMENT")
    run.bold = True
    run.font.size = Pt(18)
    
    doc.add_paragraph()
    doc.add_paragraph(f"Agreement No: LSA-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    doc.add_paragraph(f"Date: {datetime.now().strftime('%d %B %Y')}")
    doc.add_paragraph()
    
    # Parties
    doc.add_heading("1. PARTIES", level=1)
    doc.add_paragraph("This Agreement is entered into by:")
    doc.add_paragraph()
    p = doc.add_paragraph()
    p.add_run("SELLER: ").bold = True
    p.add_run("JTC Corporation, 8 Jurong Town Hall Road, Singapore 609434.")
    doc.add_paragraph()
    p = doc.add_paragraph()
    p.add_run("BUYER: ").bold = True
    p.add_run(f"{buyer}, a company incorporated in Singapore.")
    doc.add_paragraph()
    
    # Recitals
    doc.add_heading("2. RECITALS", level=1)
    doc.add_paragraph(f"WHEREAS the Seller owns the land at {parcel.get('name', 'the Property')}, Singapore {parcel.get('postal_code', '')} ('Property');")
    doc.add_paragraph("WHEREAS the Buyer wishes to purchase the Property for industrial development;")
    doc.add_paragraph("NOW THEREFORE, parties agree as follows:")
    doc.add_paragraph()
    
    # Property Description
    doc.add_heading("3. PROPERTY DESCRIPTION", level=1)
    table = doc.add_table(rows=6, cols=2)
    table.style = 'Table Grid'
    details = [
        ("Property Name", parcel.get('name', 'N/A')),
        ("Postal Code", parcel.get('postal_code', 'N/A')),
        ("Land Area", parcel.get('land_area', 'N/A')),
        ("Zoning", parcel.get('zoning', 'N/A')),
        ("Chemical Processing", "Permitted" if parcel.get('chemical_allowed') else "Not Permitted"),
        ("Chemical Type", parcel.get('chemical_type', 'N/A')),
    ]
    for i, (label, value) in enumerate(details):
        table.rows[i].cells[0].text = label
        table.rows[i].cells[1].text = str(value)
        table.rows[i].cells[0].paragraphs[0].runs[0].bold = True
    doc.add_paragraph()
    
    # Purchase Price
    doc.add_heading("4. PURCHASE PRICE", level=1)
    doc.add_paragraph(f"Total price: {parcel.get('premium', 'TBD')}")
    doc.add_paragraph("Payment: 10% deposit, 20% within 30 days, 70% on completion.")
    doc.add_paragraph()
    
    # Conditions
    doc.add_heading("5. CONDITIONS", level=1)
    doc.add_paragraph("This Agreement is conditional upon:")
    doc.add_paragraph("(a) Buyer obtaining regulatory approvals;")
    doc.add_paragraph("(b) Satisfactory environmental assessment;")
    if parcel.get('chemical_allowed'):
        doc.add_paragraph("(c) Buyer obtaining NEA license for chemical processing;")
    doc.add_paragraph()
    
    # Covenants
    doc.add_heading("6. COVENANTS", level=1)
    doc.add_paragraph("Buyer shall:")
    doc.add_paragraph("(a) Commence construction within 12 months;")
    doc.add_paragraph("(b) Complete within 36 months;")
    doc.add_paragraph("(c) Obtain Green Mark Gold certification;")
    doc.add_paragraph()
    
    # Governing Law
    doc.add_heading("7. GOVERNING LAW", level=1)
    doc.add_paragraph("This Agreement is governed by Singapore law.")
    doc.add_paragraph()
    
    # Signatures
    doc.add_heading("8. SIGNATURES", level=1)
    doc.add_paragraph()
    doc.add_paragraph("For JTC Corporation:")
    doc.add_paragraph("_________________________")
    doc.add_paragraph()
    doc.add_paragraph(f"For {buyer}:")
    doc.add_paragraph("_________________________")
    
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


def upload_to_blob(postal_code: str, buyer: str = "Sample Buyer Pte Ltd") -> dict:
    """Generate agreement and upload to Azure Blob Storage."""
    if not STORAGE_CONN_STR:
        raise ValueError("Azure Storage connection string not configured")
    
    from app import LAND_PARCELS
    parcel = next((p for p in LAND_PARCELS if p["postal_code"] == postal_code), None)
    if not parcel:
        raise ValueError(f"Parcel {postal_code} not found")
    
    doc_buffer = generate_land_sales_agreement(parcel, buyer)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"LSA_{postal_code}_{timestamp}.docx"
    
    blob_service = BlobServiceClient.from_connection_string(STORAGE_CONN_STR)
    container = blob_service.get_container_client(STORAGE_CONTAINER)
    try:
        container.create_container()
    except:
        pass
    
    blob = container.get_blob_client(filename)
    doc_buffer.seek(0)
    blob.upload_blob(doc_buffer, overwrite=True, content_settings={
        'content_type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    })
    
    sas = generate_blob_sas(
        account_name=blob_service.account_name,
        container_name=STORAGE_CONTAINER,
        blob_name=filename,
        account_key=blob_service.credential.account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.utcnow() + timedelta(hours=24)
    )
    
    return {"sas_url": f"{blob.url}?{sas}", "filename": filename, "expires_in": "24 hours"}


def generate_local(postal_code: str, buyer: str = "Sample Buyer Pte Ltd") -> str:
    """Generate agreement and save locally."""
    from app import LAND_PARCELS
    parcel = next((p for p in LAND_PARCELS if p["postal_code"] == postal_code), None)
    if not parcel:
        raise ValueError(f"Parcel {postal_code} not found")
    
    doc_buffer = generate_land_sales_agreement(parcel, buyer)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filepath = os.path.join(os.path.dirname(__file__), 'generated_docs', f"LSA_{postal_code}_{timestamp}.docx")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    with open(filepath, 'wb') as f:
        f.write(doc_buffer.getvalue())
    
    return filepath
