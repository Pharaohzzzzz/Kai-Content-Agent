"""Validate editorial drafts, rank candidates, and render the operating brief."""
import re
from difflib import SequenceMatcher

from research_collect import aihot_link, checked_url, publication, required_text, title_key, url_key


WEIGHTS = {
    "information_value": 25, "audience_fit": 25, "timeliness": 15, "originality": 15,
    "sharing_potential": 10, "business_value": 5, "production_feasibility": 5,
}
CLAIM_LABELS = {
    "confirmed": "已确认事实", "party_statement": "当事方声明", "media_report": "媒体报道",
    "unverified": "未证实信息", "kai_analysis": "Kai原创判断",
}
EVIDENCE_KINDS = {"primary", "media", "community", "other"}
RIGHTS = {"owned", "authorized", "unknown", "pending", "denied"}
ALLOW_RIGHTS = {"owned", "authorized"}
AI_LABELS = {"required", "not-required", "unknown"}
READY = "ready"
TOPIC_STATES = {
    "candidate": {"selected", "rejected"}, "selected": {"produced", "rejected"},
    "produced": {"published"}, "published": set(), "rejected": set(),
}


def allowed(value, values, label):
    if not isinstance(value, str) or value not in values:
        raise ValueError(f"Invalid {label}: {value}")
    return value


def bool_field(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return value


def topic_url(value):
    url = checked_url(value)
    if aihot_link(url):
        raise ValueError("AIHOT is disabled pending written authorization")
    return url


def nonempty_list(value, label):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty array")
    return value


def facts_for(raw, event_url):
    evidence = {}
    for item in nonempty_list(raw["evidence"], "evidence"):
        url = topic_url(item["url"])
        key = url_key(url)
        if key in evidence:
            raise ValueError(f"Duplicate evidence URL: {url}")
        evidence[key] = dict(
            url=url, title=required_text(item["title"], "evidence title"),
            source=required_text(item["source"], "evidence source"),
            kind=allowed(item["kind"], EVIDENCE_KINDS, "evidence kind"),
            access=allowed(item["access"], {"opened", "link_only"}, "evidence access"),
            published_at=publication(item.get("published_at")),
        )
    if url_key(event_url) not in evidence:
        raise ValueError("event_url must appear in evidence")
    claims, gaps = [], []
    for claim in nonempty_list(raw["claims"], "claims"):
        role = allowed(claim["role"], CLAIM_LABELS, "claim role")
        statement = required_text(claim["statement"], "claim statement")
        core = bool_field(claim["core"], "claim core")
        claim_urls = claim["urls"]
        if not isinstance(claim_urls, list):
            raise ValueError("claim urls must be an array")
        sources = []
        for url in claim_urls:
            source = evidence.get(url_key(topic_url(url)))
            if not source:
                raise ValueError(f"Claim URL missing from evidence: {url}")
            sources.append(source)
        opened = {source["kind"] for source in sources if source["access"] == "opened"}
        match = {"confirmed": "primary", "party_statement": "primary", "media_report": "media"}
        if role in match and match[role] not in opened:
            raise ValueError(f"{role} claim needs corresponding opened evidence: {statement}")
        if core and role == "kai_analysis":
            raise ValueError("Kai analysis cannot be a core fact")
        if core and role in {"unverified", "media_report"}:
            gaps.append(statement)
        claims.append(dict(role=role, core=core, statement=statement, urls=[source["url"] for source in sources]))
    if not any(claim["core"] for claim in claims):
        raise ValueError("At least one core factual claim is required")
    if evidence[url_key(event_url)]["access"] != "opened":
        gaps.append("原始事件链接尚未读到正文。")
    return list(evidence.values()), claims, gaps


def material_fields(item):
    return dict(
        rights=allowed(item["rights"], RIGHTS, "material rights"),
        rights_note=required_text(item["rights_note"], "material rights_note"),
        ai_label=allowed(item["ai_label"], AI_LABELS, "material AI label"),
    )


def production_for(root, line, raw):
    production = dict(
        format=required_text(raw["format"], "format"),
        difficulty=required_text(raw["difficulty"], "difficulty"),
        risk=required_text(raw["risk"], "risk"),
    )
    duration = raw["duration_seconds"]
    if type(duration) is not int or duration < 1:
        raise ValueError("duration_seconds must be a positive integer")
    production["duration_seconds"] = duration
    if line == "A":
        interview = raw["interview"]
        local_path = interview.get("local_path")
        available = bool_field(interview["has_legal_local_material"], "has_legal_local_material")
        use = material_fields(interview)
        interview_url = topic_url(interview["interview_url"])
        media_url = topic_url(interview["media_url"])
        if local_path:
            local_path = required_text(local_path, "local_path")
        if available and (
            use["rights"] not in ALLOW_RIGHTS or not local_path or not (root / local_path).is_file()
        ):
            raise ValueError("A legal local interview requires confirmed rights and an existing file")
        production["interview"] = dict(
            interview_url=interview_url, people=required_text(interview["people"], "interview people"),
            context=required_text(interview["context"], "interview context"), media_url=media_url,
            planned_segment=required_text(interview["planned_segment"], "planned_segment"),
            has_legal_local_material=available, local_path=local_path, **use,
        )
        ready = available and use["rights"] in ALLOW_RIGHTS
        return production, ready
    materials = []
    for item in nonempty_list(raw["materials"], "materials"):
        kind = allowed(item["kind"], {"screen_recording", "chart", "original_text", "visual"}, "material kind")
        details = material_fields(item)
        source_url = item.get("source_url")
        if source_url:
            source_url = topic_url(source_url)
        materials.append(dict(
            kind=kind, description=required_text(item["description"], "material description"),
            essential=bool_field(item["essential"], "essential material"),
            availability=allowed(item["availability"], {"ready", "planned", "missing"}, "material availability"),
            source_url=source_url, **details,
        ))
    required = [item for item in materials if item["essential"]]
    if not required:
        raise ValueError("B production must identify essential materials")
    ready = all(
        item["rights"] in ALLOW_RIGHTS and (
            item["availability"] == "ready" or item["availability"] == "planned" and item["rights"] == "owned"
        )
        for item in required
    )
    production.update(script_direction=required_text(raw["script_direction"], "script_direction"), materials=materials)
    return production, ready


def score_for(scores):
    if set(scores) != set(WEIGHTS) or any(type(val) is not int or not 0 <= val <= 100 for val in scores.values()):
        raise ValueError("Expected one integer 0–100 per scoring dimension")
    return round(sum(scores[key] * weight for key, weight in WEIGHTS.items()) / 100, 2)


def prepare_topic(root, run_id, raw, created_date):
    slug = required_text(raw["slug"], "topic slug")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Topic slug must use lowercase ASCII letters, digits, and hyphens")
    line = allowed(raw["line"], {"A", "B"}, "production line")
    event_url = topic_url(raw["event_url"])
    evidence, claims, gaps = facts_for(raw, event_url)
    production, materials_ready = production_for(root, line, raw["production"])
    verify = raw["verification_notes"]
    if not isinstance(verify, list):
        raise ValueError("verification_notes must be an array")
    verify = [required_text(note, "verification note") for note in verify]
    major_gap = bool_field(raw["major_fact_gap"], "major_fact_gap")
    if major_gap and not verify:
        raise ValueError("A major fact gap needs a verification note")
    state = "needs_verification" if gaps or major_gap else READY if materials_ready else "needs_material"
    original_view = required_text(raw["original_view"], "original_view")
    return dict(
        topic_id=f"{run_id}:{slug}", slug=slug, created_date=created_date, status="candidate",
        title=required_text(raw["title"], "title"), theme=required_text(raw["theme"], "theme"),
        event_url=event_url, line=line, evidence=evidence, claims=claims,
        audience_value=required_text(raw["audience_value"], "audience_value"), original_view=original_view,
        hook=required_text(raw["hook"], "hook"), recommendation=required_text(raw["recommendation"], "recommendation"),
        scores=raw["scores"], score=score_for(raw["scores"]),
        score_basis=required_text(raw["score_basis"], "score_basis"),
        production_state=state, materials_ready=materials_ready, production=production,
        verification_notes=verify, blocking_facts=gaps, major_fact_gap=major_gap,
        new_development=required_text(raw["new_development"], "new_development") if raw.get("new_development") else None,
        handoff={
            "line": line, "ready_to_prepare": state == READY, "topic_id": f"{run_id}:{slug}",
            "source_urls": [item["url"] for item in evidence], "original_view": original_view,
            "production": production,
        },
    )


def match_topic(topic, prior):
    if url_key(topic["event_url"]) == url_key(prior["event_url"]):
        return "event_url"
    for field in ("title", "theme"):
        left, right = (title_key(node[field]) for node in (topic, prior))
        if left and right and (
            left == right or min(len(left), len(right)) >= 6 and SequenceMatcher(None, left, right).ratio() >= 0.84
        ):
            return field
    return None


def rank_topics(topics, history, max_topics):
    kept, duplicates = [], []
    for topic in sorted(topics, key=lambda row: (-row["score"], row["slug"])):
        current = [(prior, match_topic(topic, prior)) for prior in kept]
        previous = [(prior, match_topic(topic, prior)) for prior in history]
        hits = [(prior, reason) for prior, reason in current if reason]
        historical = [(prior, reason) for prior, reason in previous if reason]
        if hits or historical and not topic["new_development"]:
            prior, reason = (hits or historical)[0]
            duplicates.append(dict(topic_id=topic["topic_id"], matched_topic_id=prior["topic_id"], field=reason))
            continue
        if historical:
            topic["revisits"] = [prior["topic_id"] for prior, _ in historical]
        if len(kept) < max_topics:
            topic["rank"] = len(kept) + 1
            kept.append(topic)
    return kept, duplicates


def md(value):
    return str(value).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


def link(url, label):
    return f"[{md(label)}](<{url}>)"


def daily_brief(result, collected):
    topic_list = result["topics"]
    labels = {"ready": "可准备制作", "needs_verification": "待核实", "needs_material": "需补素材或授权"}
    lines = [f"# Kai 每日选题｜{result['research_date']}", "",
             f"研究范围 {collected['hours']} 小时（北京时间）。共入选 {len(topic_list)} 个候选。"]
    failures = [source["name"] for source in collected["sources"] if source["status"] in {"failed", "partial"}]
    if failures:
        lines += ["", "本次采集受限：" + "、".join(failures) + "。"]
    if result["recommended_topic_id"]:
        top = next(topic for topic in topic_list if topic["topic_id"] == result["recommended_topic_id"])
        lines += ["", f"**优先制作：{md(top['title'])}**。{md(top['recommendation'])}"]
    else:
        lines += ["", "本次没有可直接准备制作的选题。"]
    if result["note"]:
        lines += ["", md(result["note"])]
    for topic in topic_list:
        plan = topic["production"]
        lines += ["", f"## {topic['rank']}. {md(topic['title'])}", "",
                  f"**{topic['score']:.2f} 分 · {topic['line']} 线 · {labels[topic['production_state']]}**"
                  f" · {md(plan['format'])} · {plan['duration_seconds']} 秒",
                  f"编号 `{topic['topic_id']}`", ""]
        by_url = {source["url"]: source for source in topic["evidence"]}
        for claim in topic["claims"]:
            sources = " · ".join(
                link(url, f"{by_url[url]['source']} {by_url[url]['published_at'] or '日期未载'}"
                     + ("，正文未读" if by_url[url]["access"] == "link_only" else ""))
                for url in claim["urls"]
            )
            lines.append(f"- {CLAIM_LABELS[claim['role']]}｜{md(claim['statement'])}" + (f" {sources}" if sources else ""))
        lines += ["", f"- **受众价值**　{md(topic['audience_value'])}",
                  f"- **Kai 观点**　{md(topic['original_view'])}", f"- **开场**　{md(topic['hook'])}",
                  f"- **推荐理由**　{md(topic['recommendation'])}"]
        if topic["line"] == "A":
            footage = plan["interview"]
            lines += [f"- **素材**　{md(footage['planned_segment'])}。权利 {footage['rights']}，"
                      f"本地合法素材{'已确认' if footage['has_legal_local_material'] else '待补充'}。"
                      f"{md(footage['rights_note'])} {link(footage['media_url'], '片源')}"]
        else:
            materials = "；".join(
                f"{md(item['description'])}（{'必要' if item['essential'] else '可选'}，"
                f"{item['rights']} / {item['availability']}）" for item in plan["materials"]
            )
            lines += [f"- **脚本**　{md(plan['script_direction'])}", f"- **素材**　{materials}"]
        lines += [f"- **制作**　{md(plan['difficulty'])}。{md(plan['risk'])}"]
        notes = topic["verification_notes"] + topic["blocking_facts"]
        if notes:
            lines += ["- **待核实**　" + "；".join(map(md, notes))]
        if topic.get("revisits"):
            lines += ["- **重新纳入的理由**　" + md(topic["new_development"])]
    lines += ["", "评分用于本次选题比较。制作建议以事实与素材准入结果为准。", ""]
    return "\n".join(lines)


def gemini_prompt(result):
    lines = ["# Gemini 手动复核", "",
             "请核对下列公开选题是否遗漏关键来源、条件或反例。逐条给出建议和原始公开链接，"
             "区分当事方说法与已证实事实。无法打开原文时注明。建议将由 Kai 另行核实。", ""]
    for topic in result["topics"]:
        lines.extend([f"## {md(topic['title'])}", md(topic["original_view"]),
                      *(f"- {link(item['url'], item['title'])}" for item in topic["evidence"]), ""])
    return "\n".join(lines)
