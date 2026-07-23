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
    out = {"fecha": fechas[0] if fechas else None, "fecha_prev": fechas[1] if len(fechas) > 1 else None, "moneda": "ARS/t"}
    nombres = {"Soja": "soja", "Trigo": "trigo", "Maíz": "maiz", "Girasol": "girasol", "Sorgo": "sorgo"}
    def precio(txt):
        m = re.search(r"([\d\.]+,\d+|[\d\.]+)", txt)
        return float(m.group(1).replace(".", "").replace(",", ".")) if m else None
    i = 0
    while i < len(tds):
        if tds[i] in nombres:
            clave = nombres[tds[i]]
            # tds[i+1] nombre en inglés; tds[i+2] precio más reciente; tds[i+3] día anterior
            out[clave] = precio(tds[i + 2]) if i + 2 < len(tds) else None
            out[clave + "_prev"] = precio(tds[i + 3]) if i + 3 < len(tds) else None
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
    q, pct = {}, {}
    for x in d["FormattedQuoteResult"]["FormattedQuote"]:
        if x.get("last"):
            q[x["symbol"]] = float(x["last"].replace(",", ""))
        if x.get("change_pct"):
            m = re.search(r"([+-]?[\d\.]+)", x["change_pct"])
            if m: pct[x["symbol"]] = float(m.group(1))
    return {
        "wti": q.get("@CL.1"), "wti_pct": pct.get("@CL.1"),
        "brent": q.get("@BZ.1"), "brent_pct": pct.get("@BZ.1"),
        "algodon": q.get("@CT.1"), "algodon_pct": pct.get("@CT.1"),      # ¢/lb
        "soja_cbot": round(q["@S.1"] * F_SOJA, 1) if q.get("@S.1") else None, "soja_cbot_pct": pct.get("@S.1"),
        "maiz_cbot": round(q["@C.1"] * F_MAIZ, 1) if q.get("@C.1") else None, "maiz_cbot_pct": pct.get("@C.1"),
        "trigo_cbot": round(q["@W.1"] * F_MAIZ, 1) if q.get("@W.1") else None, "trigo_cbot_pct": pct.get("@W.1"),
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

# ---------- Bonos USA (Tesoro, rendimientos + serie) ----------
def bonos_usa():
    hoy = datetime.date.today()
    mes_ant = (hoy.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y%m")
    serie = []
    for mes in (mes_ant, hoy.strftime("%Y%m")):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
               f"?data=daily_treasury_yield_curve&field_tdr_date_value_month={mes}")
        try:
            x = get(url)
        except Exception:
            continue
        y2s = re.findall(r"<d:BC_2YEAR[^>]*>([\d\.]+)</d:BC_2YEAR>", x)
        y10s = re.findall(r"<d:BC_10YEAR[^>]*>([\d\.]+)</d:BC_10YEAR>", x)
        fchs = re.findall(r"<d:NEW_DATE[^>]*>([^<]+)</d:NEW_DATE>", x)
        for f, a, b in zip(fchs, y2s, y10s):
            serie.append({"fecha": f[:10], "y2": float(a), "y10": float(b)})
    serie = serie[-25:]
    ult = serie[-1] if serie else {}
    return {"y2": ult.get("y2"), "y10": ult.get("y10"), "fecha": ult.get("fecha"), "serie": serie}

# ---------- Riesgo país (serie últimos 90 días) ----------
def riesgo_serie():
    d = json.loads(get("https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais"))
    return d[-90:]

# ---------- Bonos argentinos (MatbaRofex spot) ----------
def bonos_arg():
    hoy = datetime.date.today()
    desde = (hoy - datetime.timedelta(days=40)).isoformat()
    out = {}
    for spot in ("AL30", "GD30"):
        try:
            d = json.loads(get(f"https://apicem.matbarofex.com.ar/api/v2/spot-prices?spot={spot}&from={desde}&to={hoy.isoformat()}"))
            pts = [{"fecha": x["dateTime"][:10], "v": x["price"]} for x in d["data"] if x.get("price")]
            out[spot] = {"serie": pts[-30:], "ultimo": pts[-1]["v"] if pts else None,
                         "prev": pts[-2]["v"] if len(pts) > 1 else None,
                         "fecha": pts[-1]["fecha"] if pts else None}
        except Exception as e:
            print(f"[aviso] bonos_arg {spot}: {e}", file=sys.stderr)
            out[spot] = None
    return out

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
        "riesgo_serie": intento(riesgo_serie),
        "bonos_arg": intento(bonos_arg),
        "ipim": intento(ipim),
        "novillo": None,  # fuente MAG pendiente
    }
    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    print(json.dumps(datos, ensure_ascii=False, indent=1)[:800])

if __name__ == "__main__":
    main()
