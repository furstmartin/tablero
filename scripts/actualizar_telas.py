#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extractor de datos de Telas (sensibles).
Lee dos CSV publicados de Google Sheets (URLs en variables de entorno, nunca en el repo):
  - TELAS_PAGOS_URL: hoja PAGOS25 (activo, clientes, proveedor, PN)
  - TELAS_COMPRAS_URL: hoja COMPRAS V4 (pedidos)
Genera telas.enc.json cifrado con AES-256-GCM (clave en TELAS_CLAVE, derivada con PBKDF2).
Si faltan las variables, sale sin error para no romper el workflow.
"""
import base64, csv, io, json, os, re, sys, datetime, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126"}

def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")

def num(s):
    """'145.643.583' o '138.164,50' -> float (formato es-AR)."""
    if not s: return None
    s = s.strip().replace("$", "").replace(" ", "")
    m = re.match(r"^\(?-?[\d\.\,]+\)?$", s)
    if not m: return None
    neg = s.startswith("(") or s.startswith("-")
    s = s.strip("()-")
    if "," in s: s = s.replace(".", "").replace(",", ".")
    else: s = s.replace(".", "")
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None

# ---------- PAGOS25: balance ----------
def parse_pagos(txt):
    filas = list(csv.reader(io.StringIO(txt)))
    out = {"clientes": [], "bancos": []}
    CLIENTES = ["JOSE Y FABIAN", "ELIAS Y HENRY", "SERGIO Y RAUL", "MARTIN BAR", "ALEX VIANO", "JUAN", "DALMIRO", "DL"]
    BANCOS = ["BBVA", "BIND", "CAJA OFICINA ARS", "CHEQUES BBVA", "BIND INVERSIONES", "BIND CHEQUES"]
    for f in filas:
        f = [c.strip() for c in f] + [""] * 12
        a = f[0].upper()
        if a == "FECHA" and f[1]: out["fecha"] = f[1]
        if a in [c.upper() for c in CLIENTES]:
            v = num(f[1])
            if v: out["clientes"].append({"nombre": f[0].title(), "ars": v, "fecha": f[2]})
        if a in [b.upper() for b in BANCOS]:
            v = num(f[1])
            if v: out["bancos"].append({"nombre": f[0], "ars": v})
        if a == "TOTAL ACTIVO": out["total_activo"] = num(f[1])
        if a == "TOTAL PN":
            out["pn_ars"] = num(f[1]); out["pn_usd"] = num(f[2]); out["tc"] = num(f[3])
        # columnas F+ (pasivo): buscar en toda la fila
        for j, cel in enumerate(f[:-1]):
            cu = cel.upper()
            if cu == "FACTURAS FOB":
                out["proveedor_ars"] = num(f[j+1]); out["proveedor_usd"] = num(f[j+2])
            if cu.startswith("DEUDA A FAVOR"):
                out["deuda_favor_ars"] = num(f[j+1]); out["deuda_favor_usd"] = num(f[j+2])
            if cu == "TOTAL PASIVO":
                out["total_pasivo"] = num(f[j+1])
    out["clientes"].sort(key=lambda x: -x["ars"])
    return out

# ---------- COMPRAS V4: pedidos pendientes ----------
ESTADOS = ["PEDIDO CONFIRMADO", "PEDIDO SIN CONFIRMACION", "PEDIDO EN TRASITO", "PEDIDO EN TRANSITO"]

def parse_compras(txt):
    filas = list(csv.reader(io.StringIO(txt)))
    hdr_idx, hdr = None, None
    for i, f in enumerate(filas[:20]):
        if "NRO PEDIDO" in [c.strip().upper() for c in f]:
            hdr_idx, hdr = i, [c.strip().upper() for c in f]
            break
    if hdr is None: return {"pedidos": [], "error": "sin encabezado"}
    def col(nombre):
        for j, c in enumerate(hdr):
            if c == nombre: return j
        return None
    iN, iE = col("NRO PEDIDO"), col("ESTADO PEDIDO")
    iF, iD, iA = col("FACTURA"), col("DESTINO"), col("ARTICULO")
    iP, iQ = col("CANTIDAD PEDIDA"), col("CANTIDAD ENTREGADA")
    pedidos = []
    for f in filas[hdr_idx+1:]:
        f = [c.strip() for c in f] + [""] * 30
        estado = f[iE].upper() if iE is not None else ""
        if estado not in ESTADOS: continue
        pedidos.append({
            "pedido": f[iN], "estado": estado.replace("TRASITO", "TRANSITO").title(),
            "factura": f[iF] or None, "destino": f[iD], "articulo": f[iA],
            "pedida": num(f[iP]), "entregada": num(f[iQ]),
        })
    return {"pedidos": pedidos}

# ---------- Cifrado AES-256-GCM ----------
def cifrar(obj, clave):
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt = os.urandom(16)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200000)
    key = kdf.derive(clave.encode("utf-8"))
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, json.dumps(obj, ensure_ascii=False).encode("utf-8"), None)
    return {
        "v": 1, "kdf": "PBKDF2-SHA256", "iter": 200000,
        "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(),
        "data": base64.b64encode(ct).decode(),
    }

def main():
    url_pagos = os.environ.get("TELAS_PAGOS_URL")
    url_compras = os.environ.get("TELAS_COMPRAS_URL")
    clave = os.environ.get("TELAS_CLAVE")
    if not clave or not (url_pagos or url_compras):
        print("telas: sin secretos configurados, salteo")
        return
    datos = {"actualizado": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if url_pagos:
        try: datos["balance"] = parse_pagos(get(url_pagos))
        except Exception as e: print("telas pagos error:", e, file=sys.stderr)
    if url_compras:
        try: datos["compras"] = parse_compras(get(url_compras))
        except Exception as e: print("telas compras error:", e, file=sys.stderr)
    with open("telas.enc.json", "w") as f:
        json.dump(cifrar(datos, clave), f)
    print("telas.enc.json generado:", {k: (len(v.get('pedidos',[])) if k=='compras' else 'ok') for k,v in datos.items() if k!='actualizado'})

if __name__ == "__main__":
    main()
