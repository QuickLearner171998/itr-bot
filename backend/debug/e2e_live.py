"""Live end-to-end smoke test against the running backend.

Generates synthetic Form 16 and Form 26AS images, drives the full API flow
(intake -> upload/extract -> reconcile -> compute -> guidance), and prints a
summary. Requires the backend running on API_BASE with a valid OPENAI_API_KEY.

Run: .venv/bin/python -m backend.debug.e2e_live
"""

from __future__ import annotations

import io

import requests
from PIL import Image, ImageDraw

API = "http://127.0.0.1:8000"


def _doc_image(lines: list[str]) -> bytes:
    img = Image.new("RGB", (900, 60 + 26 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    y = 20
    for line in lines:
        draw.text((30, y), line, fill="black")
        y += 26
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> None:
    sid = requests.post(f"{API}/api/session").json()["session_id"]
    print("session:", sid)

    intake = requests.post(f"{API}/api/session/{sid}/intake", json={
        "age": 31, "changed_jobs": False, "has_capital_gains": False,
        "has_savings_interest": True, "claims_80c": True, "claims_80d": True,
        "has_nps": True,
    }).json()
    print("form:", intake["decision"]["form"])

    form16 = _doc_image([
        "FORM 16 - PART B  (Certificate under section 203)",
        "Employer Name: Acme Software Pvt Ltd",
        "Employer TAN: ACME12345E",
        "Gross Salary u/s 17(1): 1800000",
        "Allowances exempt u/s 10 (HRA, LTA): 150000",
        "Standard Deduction u/s 16(ia): 75000",
        "Professional Tax u/s 16(iii): 2400",
        "Deduction u/s 80C: 150000",
        "Deduction u/s 80CCD(1B): 50000",
        "Deduction u/s 80D: 25000",
        "Total TDS deducted: 250000",
        "Tax regime opted: New",
    ])
    r = requests.post(f"{API}/api/session/{sid}/documents",
                      data={"doc_type": "form16"},
                      files={"file": ("form16.png", form16, "image/png")})
    ext = r.json()
    print("form16 status:", ext["status"], "conf:", ext["overall_confidence"])
    for f in ext["fields"]:
        print("   ", f["label"], "=", f["value"], f"({f['confidence']:.2f})")

    form26 = _doc_image([
        "FORM 26AS - Annual Tax Statement  AY 2026-27",
        "TDS on Salary (Part A): 250000",
        "TDS on Other Income: 0",
        "Advance Tax Paid: 0",
        "Self Assessment Tax: 0",
    ])
    requests.post(f"{API}/api/session/{sid}/documents",
                  data={"doc_type": "form26as"},
                  files={"file": ("f26as.png", form26, "image/png")})

    recon = requests.post(f"{API}/api/session/{sid}/reconcile").json()
    print("reconcile issues:", len(recon["issues"]))

    comp = requests.post(f"{API}/api/session/{sid}/compute").json()
    c = comp["computation"]
    print("regime:", c["regime"], "tax:", c["result"]["total_tax_liability"])
    print("verified:", comp["verified"], "-", comp["verification_note"])

    guide = requests.get(f"{API}/api/session/{sid}/guidance").json()
    print("guidance sections:", len(guide["sections"]))
    print("OK")


if __name__ == "__main__":
    main()
