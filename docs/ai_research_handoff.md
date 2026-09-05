# External AI Research Handoff

The calculator can package reviewed PDF evidence for a web-enabled AI or human researcher:

```bash
PYTHONPATH=. python3 ai/research_packet.py output/review/<project>/ai_input.json
```

This creates `research_packet/` beside the supplied evidence packet. Upload the generated `ai_input.json`, `research_request.json`, `prompt.md`, and `result_template.json` to the approved research environment.

The generated code does **not** browse the internet, send project data to a third party, or apply research findings as calculation inputs. This separation prevents hidden cost, credential, privacy, and engineering-assumption decisions.

Researchers must use direct citations and record unresolved or conflicting information. An engineer must review each proposed fact before it enters a load model. The packet's result template intentionally has no `confirmed` status.
