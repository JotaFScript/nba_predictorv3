"""Genera datos SINTÉTICOS con el mismo formato que LeagueGameLog (solo para pruebas)."""
from __future__ import annotations

import random
from datetime import date, timedelta

EQ = ["ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL",
      "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR",
      "UTA", "WAS"]


def generar(primera=2019, temporadas=7, semilla=7, hasta: date | None = None):
    rnd = random.Random(semilla)
    ataque = {e: rnd.gauss(0, 3) for e in EQ}
    defensa = {e: rnd.gauss(0, 3) for e in EQ}
    ritmo = {e: rnd.gauss(0, 1.5) for e in EQ}
    filas, gid = [], 0
    for k in range(temporadas):
        anio = primera + k
        for e in EQ:                       # la calidad persiste parcialmente entre temporadas
            ataque[e] = 0.7 * ataque[e] + rnd.gauss(0, 2.2)
            defensa[e] = 0.7 * defensa[e] + rnd.gauss(0, 2.2)
        inicio = date(anio, 10, 22)
        ultimo = {e: None for e in EQ}
        dia = 0
        juegos = []
        for _ in range(82 * 30 // 2):
            pass
        # calendario: cada día juegan ~8 equipos
        cuenta = {e: 0 for e in EQ}
        while min(cuenta.values()) < 82 and dia < 175:
            fecha = inicio + timedelta(days=dia)
            libres = [e for e in EQ if cuenta[e] < 82]
            rnd.shuffle(libres)
            for i in range(0, min(len(libres), 12) - 1, 2):
                juegos.append((fecha, libres[i], libres[i + 1]))
                cuenta[libres[i]] += 1
                cuenta[libres[i + 1]] += 1
            dia += 1
        for fecha, l, v in juegos:
            if hasta and fecha > hasta:
                continue
            gid += 1
            cansancio = lambda e: -1.8 if ultimo[e] and (fecha - ultimo[e]).days == 1 else 0.0
            pace = 99 + ritmo[l] + ritmo[v] + rnd.gauss(0, 2.5)
            o_l = 113 + ataque[l] - defensa[v] + 1.4 + cansancio(l)
            o_v = 113 + ataque[v] - defensa[l] - 1.4 + cansancio(v)
            pts_l = round(pace / 100 * o_l + rnd.gauss(0, 8.3))
            pts_v = round(pace / 100 * o_v + rnd.gauss(0, 8.3))
            if pts_l == pts_v:
                pts_l += 1
            ultimo[l] = ultimo[v] = fecha
            for eq, rival, pts, opp, casa in ((l, v, pts_l, pts_v, True), (v, l, pts_v, pts_l, False)):
                fta = 22
                fga = round(pace - 0.44 * fta + 10 - 13)
                filas.append({
                    "SEASON_ID": f"2{anio}", "TEAM_ID": 1000 + EQ.index(eq), "TEAM_ABBREVIATION": eq,
                    "TEAM_NAME": eq + " Team", "GAME_ID": f"002{gid:07d}", "GAME_DATE": fecha.isoformat(),
                    "MATCHUP": f"{eq} vs. {rival}" if casa else f"{eq} @ {rival}",
                    "WL": "W" if pts > opp else "L", "MIN": 240, "PTS": pts, "FGA": fga, "FTA": fta,
                    "OREB": 10, "TOV": 13, "PLUS_MINUS": pts - opp,
                })
    return filas
