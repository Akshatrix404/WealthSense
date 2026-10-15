#!/usr/bin/env python3
"""Summarise JUnit XML from `./gradlew :parser-core:test` into a per-bank pass table.
Usage: python3 scripts/summarize_parser_tests.py android-app/parser-core/build/test-results/test"""
import sys, glob, xml.etree.ElementTree as ET
tot = fail = 0
for f in sorted(glob.glob(sys.argv[1] + "/*.xml")):
    r = ET.parse(f).getroot(); t = int(r.get("tests")); fl = int(r.get("failures")) + int(r.get("errors"))
    tot += t; fail += fl
    print(f"{r.get('name').split('.')[-1]:50s} tests={t:3d} failed={fl}")
print(f"TOTAL test methods={tot} failed={fail}")
