"""Read-only seating from saved page readings and calibrated PDF vectors. No provider calls or job writes."""
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from statistics import median
import hashlib
import json
import threading

from ai import seating
from ai.room_use_resolution import plan_text_translation
from backend.job_runner import read_json

_CACHE = {}
_LOCK = threading.Lock()


def _words(page):
    return [{"text": word["text"], "bbox": [word["x0"],word["top"],word["x1"],word["bottom"]]} for word in page.extract_words()]


def _objects(page, dx=0, dy=0):
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union
    output = []
    words = page.extract_words()
    labels = [word for word in words if str(word["text"]).casefold() in {"banquette","bench"}]
    for obj in page.rects + page.curves:
        path = obj.get("path", [])
        pts = obj.get("pts", [])
        closed = obj["object_type"] == "rect" or any(part[0] == "h" for part in path) or bool(len(pts)>2 and pts[0]==pts[-1])
        # Detailed repeated U-shaped chair bodies may have a separately drawn back closing their open side.
        glyph = len(pts)>=7 and len(path)>=7 and all(part[0] in {"m","l"} for part in path)
        bounds = [obj["x0"]+dx,obj["top"]+dy,obj["x1"]+dx,obj["bottom"]+dy]
        bench = any(obj["x0"]-20<=word["x0"]<=obj["x1"]+20 and obj["top"]-20<=word["top"]<=obj["bottom"]+20 for word in labels)
        output.append({"bbox": bounds,"closed":closed,"chair_glyph":glyph,"bench":bench,
                       "family":"rect" if obj["object_type"]=="rect" else ("glyph" if glyph else "curve")})
    lines = [LineString([(line["x0"]+dx,line["top"]+dy),(line["x1"]+dx,line["bottom"]+dy)]) for line in page.lines if line["width"]+line["height"]>0]
    if lines:
        for polygon in polygonize(unary_union(lines)):
            # Plain rectangular line cells are often floor tiles, not chair symbols.
            if len(polygon.exterior.coords) < 8:
                continue
            output.append({"bbox":list(polygon.bounds),"closed":True,
                           "family":f"line_polygon:{len(polygon.exterior.coords)}","bench":False})
    return output


def gather(root, room_use):
    root = Path(root)
    inputs = read_json(root / "ai_input.json")
    source = inputs.get("source_pdf") or inputs.get("source_files",{}).get("pdf")
    files = [root/name for name in ("reviewer_room_geometry.json","vector_geometry.json","spatial_ocr.json","page_inventory.json","ai_input.json")]
    if source:
        files.append(Path(source))
    signature = []
    for path in files:
        try:
            stat=path.stat();signature.append((str(path),stat.st_size,stat.st_mtime_ns))
        except OSError:
            signature.append((str(path),None))
    key = hashlib.sha256(json.dumps([signature,[{key:row.get(key) for key in ("room_id","original_label","taxonomy_id","space_scope","level_name")} for row in room_use.get("records",[]) if isinstance(row,dict)],seating.POLICY],sort_keys=True).encode()).hexdigest()
    with _LOCK:
        if key in _CACHE:
            return deepcopy(_CACHE[key])
    value = _gather(root,source,room_use)
    with _LOCK:
        _CACHE[key]=deepcopy(value)
        while len(_CACHE)>6:
            _CACHE.pop(next(iter(_CACHE)))
    return value


def _gather(root, source, room_use):
    traces=read_json(root / "reviewer_room_geometry.json").get("records",[])
    by_id={row["room_id"]:row for row in room_use.get("records",[]) if isinstance(row,dict) and row.get("room_id")}
    inventory=read_json(root / "page_inventory.json").get("pages",{})
    spatial=read_json(root / "spatial_ocr.json").get("pages",[])
    systems={p["page"]:p.get("coordinate_systems",{}).get("image_px",{}) for p in read_json(root / "vector_geometry.json").get("geometry_key_points",{}).get("pages",[])}
    rooms={}
    by_page=defaultdict(list)
    for trace in traces:
        rid=trace.get("room_id");use=by_id.get(rid,{})
        if use.get("space_scope") not in {"comfort_hvac","comfort_hvac_with_process_exception"}:
            continue
        if not trace.get("points_image_px") or trace.get("freshness")=="stale" or trace.get("stand_in"):
            continue
        room=rooms.setdefault(rid,{"id":rid,"name":use["original_label"],"use":use["taxonomy_id"],"scope":use["space_scope"],"level":use.get("level_name",""),"area_m2":0})
        room["area_m2"]+=trace.get("ai_measured_area_m2",0) or 0
        by_page[trace["page"]].append(trace)
    totals=[]
    warnings=[]
    levels=sorted({row["level"] for row in rooms.values()})
    def add_totals(text, page, level="", room="", structured=None):
        if not level and len(levels)==1:
            level=levels[0]
        if level not in levels:
            return
        if type(structured) is int and 0 < structured <= 5000:
            reading={"totals":[structured],"warnings":[],"kind":"structured"}
        else:
            reading=seating.printed_seating(text)
        for reason in reading["warnings"]:
            warnings.append({"page":page,"room":room or None,"reason":reason})
        for count in reading["totals"]:
            totals.append({"page":page,"total":count,"level":level,"room":room,"excerpt":text[:500],
                           "source":reading["kind"],"priority":{"structured":3,"explicit_total":2}.get(reading["kind"],1)})
    for number,page in sorted(inventory.items()):
        if page.get("page_type") in {"render_or_photo","existing_or_demolition"} or page.get("floor_plan_kind") in {"existing_or_demolition","outdoor_or_context"}:
            continue
        schedule_text=defaultdict(list)
        for item in page.get("information",[]):
            if item.get("kind")!="people_occupancy" or item.get("seat_scope") in {"outdoor","mixed_or_unknown"}:
                continue
            text=str(item.get("what",""))+" "+str(item.get("evidence",""))
            if any(word in text.casefold() for word in ("approx", "illustrative", "outdoor", "terrace")):
                continue
            label=item.get("seat_room")
            matches=[row["id"] for row in rooms.values() if row["name"].casefold()==str(label).casefold()]
            if item.get("seat_scope")=="indoor_room" and len(matches)!=1:
                warnings.append({"page":int(number),"reason":"Printed room seat total could not be assigned unambiguously; not applied to the tenancy."})
                continue
            room=matches[0] if len(matches)==1 else ""
            if item.get("seat_total") is not None:
                add_totals(text,int(number),page.get("level", ""),room,item["seat_total"])
            else:
                # Evidence often quotes the same schedule row as `what`; it is
                # provenance, not another row to add to the seat total.
                what=str(item.get("what", ""))
                parsed=seating.printed_seating(what)
                schedule_text[room].append(what if parsed["totals"] or parsed["warnings"] else text)
        for room,text in schedule_text.items():
            add_totals("\n".join(text),int(number),page.get("level", ""),room)
    for page in spatial:
        reading=inventory.get(str(page.get("page")),{})
        # Detail notes and elevations often contain seat dimensions, not capacities.
        if reading.get("page_type") not in {"floor_plan","schedule"} or reading.get("floor_plan_kind") in {"existing_or_demolition","outdoor_or_context"}:
            continue
        items=page.get("standalone_text_items",[]) or page.get("word_samples",[])
        lines=defaultdict(list)
        for item in items:
            bounds=item.get("bbox")
            if bounds:
                lines[round(bounds[1]/3)].append(item)
        text_lines=[]
        for _,line in sorted(lines.items()):
            text=" ".join(str(item.get("text","")) for item in sorted(line,key=lambda item:item["bbox"][0]))
            if not any(word in text.casefold() for word in ("outdoor","terrace","approx")):
                text_lines.append(text)
        add_totals("\n".join(text_lines),page["page"],reading.get("level", ""))
    # A structured pass-1 total wins over text parsing; explicit totals win over rows.
    priorities={}
    for row in totals:
        key=(row["level"],row["room"])
        priorities[key]=max(priorities.get(key,0),row["priority"])
    totals=[row for row in totals if row["priority"]==priorities[(row["level"],row["room"])]]
    drawings, page_counts = {}, []
    if source and Path(source).exists():
        import pdfplumber
        with pdfplumber.open(source) as doc:
            for target,traced in sorted(by_page.items()):
                system=systems.get(target,{})
                scales=[t.get("calibration",{}).get("mm_per_px") for t in traced if t.get("calibration",{}).get("status") in {"agreed","confirmed","reviewer_confirmed"}]
                scales=[x for x in scales if isinstance(x,(int,float)) and x>0]
                if not scales or not system.get("image_width") or not 0<target<=len(doc.pages):
                    warnings.append({"page":target,"reason":"No confirmed scale/image frame; seats not counted."});continue
                target_page=doc.pages[target-1]
                sx,sy=system["image_width"]/target_page.width,system["image_height"]/target_page.height
                frame=[{"id":t["room_id"],"points":[[p[0]/sx,p[1]/sy] for p in t["points_image_px"]]} for t in traced if rooms[t["room_id"]]["use"] in {"dining","retail"}]
                candidates=[target]+[int(number) for number,reading in sorted(inventory.items()) if reading.get("page_type")=="floor_plan"
                    and reading.get("floor_plan_kind") in {"furniture_or_equipment","proposed_layout"} and int(number)!=target
                    and (not reading.get("level") or reading["level"]==rooms[traced[0]["room_id"]]["level"])]
                best=None
                for number in candidates:
                    if not 0<number<=len(doc.pages):continue
                    sheet=doc.pages[number-1]
                    if not sheet.lines and not sheet.curves and not sheet.rects:
                        warnings.append({"page":number,"reason":"Image-only plan: seats not counted."});continue
                    registration=None
                    if number!=target:
                        if abs(sheet.width-target_page.width)>1 or abs(sheet.height-target_page.height)>1:continue
                        registration=plan_text_translation(_words(sheet),_words(target_page))
                        if not registration:
                            warnings.append({"page":number,"reason":"Furniture/layout plan could not be registered; seats not counted."});continue
                    if sheet.images:
                        warnings.append({"page":number,"reason":"Plan contains raster furniture: vector seat count may be incomplete; raster seats not counted."})
                    counted=seating.count_symbols(_objects(sheet,(registration or {}).get("dx_pt",0),(registration or {}).get("dy_pt",0)),frame,median(scales)*sx)
                    result={"page":number,"target_page":target,"counts":counted["counts"],"excluded_outside":counted["excluded"],
                            "seats":counted["seats"],"registration":registration,"reason":counted["reason"]}
                    page_counts.append(result)
                    if best is None or sum(result["counts"].values())>sum(best["counts"].values()):best=result
                if best:
                    for rid,count in best["counts"].items():
                        candidate={"count":count,"page":best["page"],"evidence":[{"page":best["page"],"excerpt":f"{count} repeated chair/stool symbols or labelled bench places inside the room outline.","method":"vector_seating_estimate"}]}
                        if count < 3:
                            warnings.append({"page":best["page"],"room":rid,"reason":"Only one or two candidate symbols: incomplete seating evidence; density fallback retained."})
                        elif count>drawings.get(rid,{}).get("count",0):drawings[rid]=candidate
    else:
        warnings.append({"reason":"PDF unavailable; vector seats not counted."})
    totals=sorted({json.dumps(row,sort_keys=True):row for row in totals}.values(),key=lambda row:(row["level"],row["page"],row["total"]))
    for level in levels:
        values=sorted({row["total"] for row in totals if row["level"]==level and not row["room"]})
        if len(values)>1:
            warnings.append({"level":level,"reason":f"Conflicting printed seat totals {values}; printed total not applied."})
    return {"policy":seating.POLICY,"room_inputs":list(rooms.values()),"rooms":seating.allocate(list(rooms.values()),drawings,totals),"drawn":drawings,
            "printed":totals,"page_counts":page_counts,"warnings":warnings,"basis":seating.BASIS}
