#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Actualizador de datos del Tablero.
Corre cada hora en GitHub Actions y genera datos.json con las variables
que no tienen API consultable desde el navegador.
Solo usa la biblioteca estándar de Python.
"""
import json, re, sys, time, datetime, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"}

def get(url, timeout=25, encoding="utf-8"):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(encoding, errors="ignore")

def intento(fn):
    """Ejecuta una fuente; si falla devuelve None sin romper el resto."""
    try:
        return fn()
    except Exception as e:
        print(f"[aviso] {fn.__name__} falló: {e}", file=sys.stderr)
        return None

# ---------- Pizarra Rosario (BCR, cotizaciones locales, ARS/t) ----------
def pizarra_rosario():
    h = get("https://www.bcr.com.ar/es/mercados/mercado-de-granos/cotizaciones/cotizaciones-locales-0")
    ths = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<th[^>]*>(.*?)</th>", h, re.S)]
    fechas = [t for t in ths if re.match(r"\d{2}/\d{2}/\d{4}", t)]
    tds = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", h, re.S)]
    tds = [t for t in tds if t]
    out = {"fecha": fechas[0] if fechas else None, "moneda": "ARS/t"}
    nombres = {"Soja": "soja", "Trigo": "trigo", "Maíz": "maiz", "Girasol": "girasol", "Sorgo": "sorgo"}
    i = 0
    while i < len(tds):
        if tds[i] in nombres:
            clave = nombres[tds[i]]
            # tds[i+1] es el nombre en inglés; tds[i+2] es el precio más reciente
            precio = tds[i + 2] if i + 2 < len(tds) else "S/C"
            m = re.search(r"([\d\.]+,\d+|[\d\.]+)", precio)
            out[clave] = float(m.group(1).replace(".", "").replace(",", ".")) if m else None
            i += 7
        else:
            i += 1
    return out

# ---------- Yahoo Finance (futuros) ----------
def yahoo(ticker):
    """Consulta con pausa y reintento (Yahoo limita ráfagas de pedidos)."""
    for intento_n in range(3):
        try:
            time.sleep(5)
            d = json.loads(get(f"https://query{1 + intento_n % 2}.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"))
            return d["chart"]["result"][0]["meta"].get("regularMarketPrice")
        except Exception as e:
            if "429" in str(e) and intento_n < 2:
                time.sleep(20)
            elif intento_n == 2:
                raise
    return None

F_SOJA = 0.367437   # ¢/bushel -> USD/t (soja)
F_MAIZ = 0.393683   # ¢/bushel -> USD/t (maíz y trigo)

def cnbc():
    """CNBC: los 6 futuros en una sola llamada."""
    url = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
           "?symbols=%40CL.1%7C%40BZ.1%7C%40CT.1%7C%40S.1%7C%40C.1%7C%40W.1"
           "&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")
    d = json.loads(get(url))
    q = {x["symbol"]: float(x["last"].replace(",", "")) for x in d["FormattedQuoteResult"]["FormattedQuote"] if x.get("last")}
    return {
        "wti": q.get("@CL.1"),
        "brent": q.get("@BZ.1"),
        "algodon": q.get("@CT.1"),                                       # ¢/lb
        "soja_cbot": round(q["@S.1"] * F_SOJA, 1) if q.get("@S.1") else None,
        "maiz_cbot": round(q["@C.1"] * F_MAIZ, 1) if q.get("@C.1") else None,
        "trigo_cbot": round(q["@W.1"] * F_MAIZ, 1) if q.get("@W.1") else None,
    }

def futuros():
    out = intento(cnbc)
    if out and out.get("wti") is not None:
        return out
    # Respaldo: Yahoo Finance de a un ticker
    out = {
        "wti": intento(lambda: yahoo("CL=F")),
        "brent": intento(lambda: yahoo("BZ=F")),
        "algodon": intento(lambda: yahoo("CT=F")),           # ¢/lb
        "soja_cbot": intento(lambda: yahoo("ZS=F")),
        "maiz_cbot": intento(lambda: yahoo("ZC=F")),
        "trigo_cbot": intento(lambda: yahoo("ZW=F")),
    }
    if out["soja_cbot"]: out["soja_cbot"] = round(out["soja_cbot"] * F_SOJA, 1)
    if out["maiz_cbot"]: out["maiz_cbot"] = round(out["maiz_cbot"] * F_MAIZ, 1)
    if out["trigo_cbot"]: out["trigo_cbot"] = round(out["trigo_cbot"] * F_MAIZ, 1)
    return out

# ---------- Bonos USA (Tesoro, rendimientos) ----------
def bonos_usa():
    mes = datetime.date.today().strftime("%Y%m")
    url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
           f"?data=daily_treasury_yield_curve&field_tdr_date_value_month={mes}")
    x = get(url)
    y2 = re.findall(r"<d:BC_2YEAR[^>]*>([\d\.]+)</d:BC_2YEAR>", x)
    y10 = re.findall(r"<d:BC_10YEAR[^>]*>([\d\.]+)</d:BC_10YEAR>", x)
    fechas = re.findall(r"<d:NEW_DATE[^>]*>([^<]+)</d:NEW_DATE>", x)
    return {"y2": float(y2[-1]) if y2 else None,
            "y10": float(y10[-1]) if y10 else None,
            "fecha": fechas[-1][:10] if fechas else None}

# ---------- IPIM (inflación mayorista, INDEC vía datos.gob.ar) ----------
def ipim():
    d = json.loads(get("https://apis.datos.gob.ar/series/api/series/?ids=448.1_NIVEL_GENERAL_0_0_13_46&limit=14&sort=desc&format=json"))
    filas = d["data"]  # [fecha, indice] descendente
    serie = []
    for i in range(len(filas) - 1):
        f_act, v_act = filas[i][0], filas[i][1]
        v_ant = filas[i + 1][1]
        if v_act and v_ant:
            serie.append({"fecha": f_act, "valor": round((v_act / v_ant - 1) * 100, 1)})
    serie = serie[:12][::-1]  # últimos 12, ascendente
    return {"serie": serie, "ultimo": serie[-1] if serie else None}

# ---------- Salida ----------
def main():
    datos = {
        "actualizado": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "granos_ros": intento(pizarra_rosario),
        "futuros": intento(futuros),
        "bonos_usa": intento(bonos_usa),
        "ipim": intento(ipim),
        "novillo": None,  # fuente MAG pendiente
    }
    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    print(json.dumps(datos, ensure_ascii=False, indent=1)[:800])

if __name__ == "__main__":
    main()
