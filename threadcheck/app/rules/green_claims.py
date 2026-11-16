"""Rule-based checks derived from published green-claims guidance.

Principles paraphrased from the UK CMA Green Claims Code and the EU Green Claims / Empowering
Consumers directives: claims must be truthful, clear, substantiated, not omit material information,
and generic environmental terms need specific backing. These run as a deterministic tool the agent
calls, so every flag is explainable and unit-testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Flag:
    rule_id: str
    title: str
    detail: str


VAGUE_TERMS = ("eco-friendly", "eco friendly", "sustainable", "green", "conscious", "responsible",
               "natural", "planet-friendly", "planet friendly", "ethical", "clean")
ABSOLUTE_TERMS = ("100%", "fully", "completely", "entirely", "zero", "no ", "free of", "all of our")
CARBON_TERMS = ("carbon neutral", "carbon-neutral", "net zero", "net-zero", "climate neutral", "climate positive",
                "carbon negative")
CERT_TERMS = ("gots", "oeko-tex", "oeko tex", "grs", "global recycled standard", "bluesign", "fair trade",
              "fairtrade", "b corp", "cradle to cradle", "fsc", "rws", "responsible wool")


def check_claim(claim: str, evidence_text: str = "") -> list[Flag]:
    c = claim.lower()
    ev = evidence_text.lower()
    flags: list[Flag] = []

    if any(t in c for t in VAGUE_TERMS) and not re.search(r"\d", c) and not any(t in c for t in CERT_TERMS):
        flags.append(Flag("GC-01", "Generic term without specifics",
                          "Uses a broad environmental term (e.g. 'sustainable', 'eco-friendly') with no measurable "
                          "attribute, quantity, or standard. Guidance treats such claims as likely misleading."))

    if any(t in c for t in ABSOLUTE_TERMS):
        if not re.search(r"\d+(\.\d+)?\s*%|\btonnes?\b|\blitres?\b|\bkg\b|audit|verified|third[- ]party", ev):
            flags.append(Flag("GC-02", "Absolute claim lacks quantified substantiation",
                              "Absolute wording ('100%', 'zero', 'fully') requires quantified, verifiable evidence "
                              "covering the whole product or scope claimed."))

    if any(t in c for t in CARBON_TERMS):
        if not re.search(r"offset|scope\s*[123]|verified|standard|methodolog|credit", ev):
            flags.append(Flag("GC-03", "Carbon claim without methodology disclosure",
                              "Carbon-neutral or net-zero claims must disclose the accounting scope and whether the result "
                              "relies on offsets, including offset quality."))

    if re.search(r"\d+(\.\d+)?\s*%", c) and not re.search(r"\b(of|in|across|by)\b", c):
        flags.append(Flag("GC-04", "Percentage without a clear denominator",
                          "A percentage is stated without saying what it is a percentage of (product, collection, "
                          "fibre content, emissions baseline)."))

    if any(t in c for t in CERT_TERMS):
        if not re.search(r"licen[cs]e|certificate|cert(ified)?\s*(no|number|id)|scope certificate|\b[A-Z]{2,5}-?\d{3,}\b",
                         evidence_text):
            flags.append(Flag("GC-05", "Certification named without a traceable certificate",
                              "A standard or certification is invoked but no licence or certificate reference appears "
                              "in the evidence."))

    if re.search(r"\b(more|less|better|lower|reduced|fewer)\b", c) and not re.search(r"\b(than|compared|versus|vs)\b", c):
        flags.append(Flag("GC-06", "Comparative claim without a baseline",
                          "Comparative language is used without stating the comparison point or baseline year."))

    return flags
