#!/usr/bin/env python3

import json
from pathlib import Path

from ai.ai_packet import build_ai_packet, load_json
from ai.candidate_review import create_candidate_review
from ai.chatgpt_packet import DEFAULT_DPI, create_chatgpt_packet
from ai.dimension_wall_matcher import create_dimension_wall_matches
from ai.vector_geometry import create_vector_geometry
from pdf_pipeline.spatial_ocr import create_spatial_ocr

def create_confirmed_ai_packet(packet_path, decisions_path, output_path=None):
    packet_path = Path(packet_path)
    decisions_path = Path(decisions_path)
    output_path = Path(output_path or packet_path.with_name("ai_input.json"))

    packet = load_json(packet_path)
    decisions = load_json(decisions_path)
    spatial_path = create_spatial_ocr(packet_path, packet_path.with_name("spatial_ocr.json"), decisions)
    spatial_ocr = dict(load_json(spatial_path), source=str(spatial_path))

    ai_input = build_ai_packet(packet, decisions, str(decisions_path), spatial_ocr=spatial_ocr)
    output_path.write_text(json.dumps(ai_input, indent=2), encoding="utf-8")
    # Render once so vector/candidate overlays share the same high-resolution images.
    chatgpt_packet = create_chatgpt_packet(output_path, dpi=DEFAULT_DPI, zip_packet=False, stage="preparation")
    vector_path = create_vector_geometry(
        output_path,
        output_path.with_name("vector_geometry.json"),
        Path(chatgpt_packet["folder"]) / "screenshots",
        output_path.with_name("vector_overlays"),
    )
    matches_path = create_dimension_wall_matches(
        vector_path,
        output_path.with_name("dimension_wall_matches.json"),
        output_path.with_name("dimension_match_overlays"),
        Path(chatgpt_packet["folder"]) / "screenshots",
    )
    candidate_review_path = create_candidate_review(
        vector_path,
        matches_path,
        output_path.with_name("candidate_review.json"),
        output_path.with_name("candidate_overlays"),
        Path(chatgpt_packet["folder"]) / "screenshots",
    )
    # Rebuild once to include vector and candidate evidence in the manual vision packet.
    chatgpt_packet = create_chatgpt_packet(output_path, dpi=DEFAULT_DPI)

    return {
        "ai_input": str(output_path),
        "spatial_ocr": str(spatial_path),
        "vector_geometry": str(vector_path),
        "dimension_wall_matches": str(matches_path),
        "candidate_review": str(candidate_review_path),
        "chatgpt_packet": chatgpt_packet,
    }
