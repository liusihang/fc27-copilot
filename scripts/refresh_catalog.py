#!/usr/bin/env python3
"""Atomically rebuild the normalized FC27 catalog from FUT.GG public data."""
import argparse, hashlib, json, os, sqlite3, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.import_catalog import import_catalog
from scripts.validate_catalog import validate_catalog

API = "https://www.fut.gg/api/fut/players/v2/27/"
MANIFEST = "https://r2.fut.gg/27/manifest.json"
R2_BASE = "https://r2.fut.gg/27"
PAGE_SIZE = 300
OVR_MIN = 40
OVR_MAX = 99
UA = "Mozilla/5.0 (compatible; FC27-local-snapshot/2.0)"


class HttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class HttpSession:
    def __init__(self):
        self.headers = {}

    def get(self, url, params=None, timeout=30):
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, headers=self.headers)
        with urlopen(request, timeout=timeout) as response:
            return HttpResponse(json.loads(response.read().decode("utf-8")))


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def json_text(v):
    return json.dumps(v if v is not None else [], ensure_ascii=False, separators=(",", ":"))

def bool_int(v): return 1 if v else 0

def face(c,k): return (c.get("faceStatsV2") or {}).get(k)

def entity(c,k,sub): return (c.get(k) or {}).get(sub)

def role_label(r, suffix):
    pos=(r.get("slug") or "").split("-",1)[0].upper()
    return ("%s %s%s" % (pos, r.get("name") or "", suffix)).strip()

def request(session, url, params=None, retries=5):
    last=None
    for attempt in range(retries):
        try:
            r=session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last=e
            if attempt+1<retries: time.sleep(min(2**attempt,8))
    raise RuntimeError("Request failed after retries: %s" % last)

def load_mappings(session):
    m=request(session, MANIFEST)
    version=m["_version"]
    urls={}
    data={}
    for key in ("play-styles","roles"):
        if key not in m: raise RuntimeError("Manifest missing %s" % key)
        url="%s/%s.v%s.%s.json" % (R2_BASE,key,version,m[key])
        urls[key]=url; data[key]=request(session,url)
    ps=data["play-styles"]; roles=data["roles"]
    psmap={int(x["eaId"]):x["name"] for x in ps if x.get("eaId") is not None}
    plusmap={int(r["plusEaId"]):r for r in roles if r.get("plusEaId") is not None}
    plusplusmap={int(r["plusPlusEaId"]):r for r in roles if r.get("plusPlusEaId") is not None}
    return ps,roles,psmap,plusmap,plusplusmap,urls,m

def mapped_names(ids, mapping, suffix=""):
    out=[]
    for i in ids or []:
        k=int(i)
        out.append((mapping[k]+suffix) if k in mapping else "Unknown(%s)"%i)
    return json_text(out)

def mapped_roles(ids, mapping, suffix):
    out=[]
    for i in ids or []:
        k=int(i)
        out.append(role_label(mapping[k],suffix) if k in mapping else "Unknown(%s)"%i)
    return json_text(out)

def card_row(c,psmap,plusmap,plusplusmap):
    url=c.get("url")
    if url and url.startswith("/"): url="https://www.fut.gg"+url
    pids=c.get("playStyleEaIds") or []; ppids=c.get("playStylePlusEaIds") or []
    rpids=c.get("rolesPlus") or []; rppids=c.get("rolesPlusPlus") or []
    return (
      c.get("id"),c.get("eaId"),c.get("basePlayerEaId"),c.get("slug"),c.get("basePlayerSlug"),
      c.get("commonName"),c.get("cardName"),c.get("firstName"),c.get("lastName"),c.get("nickname"),
      c.get("overall"),c.get("quality"),c.get("rarityName"),c.get("rarityId"),c.get("rarityEaId"),
      c.get("position"),c.get("positionId"),json_text(c.get("alternativePositions")),json_text(c.get("alternativePositionIds")),
      entity(c,"club","name"),entity(c,"club","eaId"),entity(c,"league","name"),entity(c,"league","eaId"),
      entity(c,"nation","name"),entity(c,"nation","eaId"),c.get("gender"),c.get("foot"),c.get("weakFoot"),
      c.get("skillMoves"),c.get("height"),c.get("age"),
      face(c,"facePace"),face(c,"faceShooting"),face(c,"facePassing"),face(c,"faceDribbling"),face(c,"faceDefending"),face(c,"facePhysicality"),
      face(c,"gkFaceDiving"),face(c,"gkFaceHandling"),face(c,"gkFaceKicking"),face(c,"gkFaceReflexes"),face(c,"gkFaceSpeed"),face(c,"gkFacePositioning"),
      c.get("attributeStrength"),c.get("accelerateType"),
      json_text(pids),json_text(ppids),json_text(rpids),json_text(rppids),
      mapped_names(pids,psmap),mapped_names(ppids,psmap,"+"),mapped_roles(rpids,plusmap,"+"),mapped_roles(rppids,plusplusmap,"++"),
      c.get("totalIgs"),bool_int(c.get("isIcon")),bool_int(c.get("isHero")),bool_int(c.get("isSpecial")),bool_int(c.get("isDynamic")),
      bool_int(c.get("isEvolutionPlayerItem")),bool_int(c.get("isSbc")),bool_int(c.get("isObjective")),c.get("createdAt"),url,c.get("imageUrl"),c.get("cardImageUrl")
    )

def create_schema(con):
    con.executescript("""
PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;
CREATE TABLE cards (
 futgg_id INTEGER PRIMARY KEY, ea_id INTEGER, base_player_ea_id INTEGER, slug TEXT, base_player_slug TEXT,
 common_name TEXT, card_name TEXT, first_name TEXT, last_name TEXT, nickname TEXT,
 overall INTEGER, quality TEXT, rarity_name TEXT, rarity_id INTEGER, rarity_ea_id INTEGER,
 position TEXT, position_id INTEGER, alternative_positions TEXT, alternative_position_ids TEXT,
 club_name TEXT, club_ea_id INTEGER, league_name TEXT, league_ea_id INTEGER, nation_name TEXT, nation_ea_id INTEGER,
 gender INTEGER, foot TEXT, weak_foot INTEGER, skill_moves INTEGER, height_cm INTEGER, age INTEGER,
 pace INTEGER, shooting INTEGER, passing INTEGER, dribbling INTEGER, defending INTEGER, physicality INTEGER,
 gk_diving INTEGER, gk_handling INTEGER, gk_kicking INTEGER, gk_reflexes INTEGER, gk_speed INTEGER, gk_positioning INTEGER,
 strength INTEGER, accelerate_type TEXT,
 playstyle_ids TEXT, playstyle_plus_ids TEXT, roles_plus_ids TEXT, roles_plus_plus_ids TEXT,
 playstyle_names TEXT, playstyle_plus_names TEXT, roles_plus_names TEXT, roles_plus_plus_names TEXT,
 total_igs INTEGER, is_icon INTEGER, is_hero INTEGER, is_special INTEGER, is_dynamic INTEGER, is_evolution INTEGER, is_sbc INTEGER, is_objective INTEGER,
 created_at TEXT, futgg_url TEXT, image_url TEXT, card_image_url TEXT
);
CREATE TABLE players (
 base_player_ea_id INTEGER PRIMARY KEY, common_name TEXT, first_name TEXT, last_name TEXT, nickname TEXT,
 gender INTEGER, nation_name TEXT, nation_ea_id INTEGER, foot TEXT, height_cm INTEGER, age INTEGER,
 card_count INTEGER, min_overall INTEGER, max_overall INTEGER
);
CREATE TABLE playstyles (
 ea_id INTEGER PRIMARY KEY, futgg_id INTEGER, name TEXT NOT NULL, category_id INTEGER, category TEXT,
 who_has_it TEXT, description TEXT, plus_description TEXT, url TEXT, image_url TEXT
);
CREATE TABLE roles (
 role_id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT, position_id INTEGER, description TEXT,
 plus_ea_id INTEGER, plus_plus_ea_id INTEGER, focus_json TEXT
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX idx_cards_base_player ON cards(base_player_ea_id); CREATE INDEX idx_cards_club ON cards(club_name);
CREATE INDEX idx_cards_ea_id ON cards(ea_id); CREATE INDEX idx_cards_league ON cards(league_name);
CREATE INDEX idx_cards_name ON cards(common_name); CREATE INDEX idx_cards_nation ON cards(nation_name);
CREATE INDEX idx_cards_overall ON cards(overall); CREATE INDEX idx_cards_position ON cards(position);
CREATE INDEX idx_playstyles_name ON playstyles(name); CREATE INDEX idx_roles_name ON roles(name);
CREATE UNIQUE INDEX idx_roles_plus_ea_id ON roles(plus_ea_id) WHERE plus_ea_id IS NOT NULL;
CREATE UNIQUE INDEX idx_roles_plus_plus_ea_id ON roles(plus_plus_ea_id) WHERE plus_plus_ea_id IS NOT NULL;
""")

CARD_INSERT="INSERT OR REPLACE INTO cards VALUES (%s)" % ",".join(["?"]*65)

def build_source(out_path):
    out_path=os.path.abspath(out_path); tmp=out_path+".new"
    for suf in ("","-wal","-shm"):
        try: os.remove(tmp+suf)
        except FileNotFoundError: pass
    started=utc_now(); con=sqlite3.connect(tmp); create_schema(con)
    s=HttpSession(); s.headers.update({"User-Agent":UA,"Accept":"application/json","Referer":"https://www.fut.gg/"})
    ps,roles,psmap,plusmap,plusplusmap,urls,manifest=load_mappings(s)
    con.executemany("INSERT INTO playstyles VALUES (?,?,?,?,?,?,?,?,?,?)",[
      (x.get("eaId"),x.get("id"),x.get("name"),x.get("categoryId"),x.get("category"),x.get("whoHasIt"),x.get("playstyleDescription"),x.get("playstylePDescription"),x.get("url"),x.get("imageUrl"))
      for x in ps if x.get("eaId") is not None])
    con.executemany("INSERT INTO roles VALUES (?,?,?,?,?,?,?,?)",[
      (r.get("id"),r.get("name"),r.get("slug"),r.get("position"),r.get("description"),r.get("plusEaId"),r.get("plusPlusEaId"),json_text(r.get("focus"))) for r in roles])
    request_count=3; source_rows=0
    try:
      for ovr in range(OVR_MIN,OVR_MAX+1):
        page=1; bucket=0
        while True:
          payload=request(s,API,{"overall__gte":ovr,"overall__lte":ovr,"count":PAGE_SIZE,"page":page}); request_count+=1
          data=payload.get("data") or []
          if not data: break
          source_rows+=len(data); bucket+=len(data)
          con.executemany(CARD_INSERT,[card_row(c,psmap,plusmap,plusplusmap) for c in data]); con.commit()
          nxt=payload.get("next")
          if not nxt: break
          page=int(nxt)
        if bucket:
          unique=con.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
          print("OVR %d: %d rows | unique cards=%d"%(ovr,bucket,unique),flush=True)
      con.execute("""
INSERT INTO players
WITH ranked AS (
 SELECT c.*,ROW_NUMBER() OVER(PARTITION BY base_player_ea_id ORDER BY overall DESC,futgg_id ASC) rn
 FROM cards c WHERE base_player_ea_id IS NOT NULL
), agg AS (
 SELECT base_player_ea_id,COUNT(*) card_count,MIN(overall) min_overall,MAX(overall) max_overall
 FROM cards WHERE base_player_ea_id IS NOT NULL GROUP BY base_player_ea_id
)
SELECT r.base_player_ea_id,r.common_name,r.first_name,r.last_name,r.nickname,r.gender,r.nation_name,r.nation_ea_id,r.foot,r.height_cm,r.age,
       a.card_count,a.min_overall,a.max_overall
FROM ranked r JOIN agg a USING(base_player_ea_id) WHERE r.rn=1
""")
      cards=con.execute("SELECT COUNT(*) FROM cards").fetchone()[0]; players=con.execute("SELECT COUNT(*) FROM players").fetchone()[0]
      unknown=con.execute("SELECT COUNT(*) FROM cards WHERE playstyle_names LIKE '%Unknown(%' OR playstyle_plus_names LIKE '%Unknown(%' OR roles_plus_names LIKE '%Unknown(%' OR roles_plus_plus_names LIKE '%Unknown(%'").fetchone()[0]
      meta={"dataset":"EA SPORTS FC 27 Ultimate Team basic player/card snapshot","schema_version":"2","game":"FC 27","source":"FUT.GG","source_api":API,
            "snapshot_started_utc":started,"snapshot_finished_utc":utc_now(),"cards":str(cards),"base_players":str(players),"playstyles":str(len(psmap)),"roles":str(len(roles)),
            "playstyles_source":urls["play-styles"],"roles_source":urls["roles"],"http_requests":str(request_count),
            "manifest_version":str(manifest.get("_version", "")),
            "manifest_hash":hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "notes":"Market prices intentionally excluded. Readable PlayStyle/Role name arrays are included alongside raw EA IDs."}
      con.executemany("INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",meta.items()); con.commit()
      integrity=con.execute("PRAGMA integrity_check").fetchone()[0]
      if integrity!="ok": raise RuntimeError("SQLite integrity_check failed: %s"%integrity)
      if cards==0: raise RuntimeError("No cards downloaded")
      if unknown: raise RuntimeError("Unresolved PlayStyle/Role mappings on %d cards"%unknown)
      con.execute("PRAGMA wal_checkpoint(TRUNCATE)"); con.commit()
      print("Validation: integrity=%s, cards=%d, players=%d, playstyles=%d, roles=%d, unknown_mappings=%d, source_rows=%d, requests=%d"%
            (integrity,cards,players,len(psmap),len(roles),unknown,source_rows,request_count))
    finally: con.close()
    os.replace(tmp,out_path)
    for suf in ("-wal","-shm"):
        try: os.remove(tmp+suf)
        except FileNotFoundError: pass
    print("Updated: %s"%out_path)

def refresh_catalog(out_path):
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fc27-refresh-") as directory:
        source_path = Path(directory) / "fc27-v2.sqlite"
        build_source(source_path)
        imported = import_catalog(source_path, out_path)
    validation = validate_catalog(out_path)
    if not validation["ok"]:
        raise RuntimeError(f"normalized catalog validation failed: {validation['errors']}")
    return {"imported": imported, "validation": validation}


def main():
    p=argparse.ArgumentParser(description="Atomically refresh normalized FC27 catalog data.")
    p.add_argument("--out",default=PROJECT_ROOT / "data" / "catalog.sqlite",type=Path)
    args=p.parse_args()
    result=refresh_catalog(args.out)
    print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))
if __name__=="__main__": main()
