import random

import pytest

from trialgraph import resolve

CFG = resolve.load_config()


@pytest.mark.parametrize("raw,child,parent", [
    # strings as they appear in the registry
    ("Arena is a wholly owned subsidiary of Pfizer", "Arena", "Pfizer"),
    ("Wyeth is now a wholly owned subsidiary of Pfizer", "Wyeth", "Pfizer"),
    ("Arrow International LCC (Subsidiary of Teleflex Inc.)", "Arrow International LCC", "Teleflex Inc"),
    ("Corthera, Inc.(formerly BAS Medical, Inc.), a member of the Novartis group of companies", "Corthera, Inc", "Novartis"),
    ("Acceleron Pharma, Inc., a wholly-owned subsidiary of Merck & Co., Inc., Rahway, NJ USA",
     "Acceleron Pharma, Inc", "Merck & Co., Inc., Rahway, NJ USA"),
    ("Novartis Pharmaceuticals", "Novartis Pharmaceuticals", None),
    # variant not in this extract
    ("Corthera, Inc. (a subsidiary of Novartis)", "Corthera, Inc", "Novartis"),
])
def test_parse_corporate(raw, child, parent):
    c, p = resolve.parse_corporate(raw)
    assert (c, p) == (child, parent)


@pytest.mark.parametrize("a,b", [
    ("Dapagliflozin 10 mg", "dapagliflozin"),
    ("Dapagliflozin 10Mg Tab", "Dapagliflozin"),
    ("Dapagliflozin 10 milligrams [Farxiga]", "dapagliflozin"),
    ("SACUBITRIL - VALSARTAN (formerly LCZ696)", "Sacubitril/Valsartan"),
    ("Sacubitril / Valsartan", "sacubitril+valsartan"),
])
def test_name_key_folds_dose_form_brackets(a, b):
    assert resolve.name_key(a) == resolve.name_key(b)


def test_sponsor_key_strips_legal_suffix():
    assert resolve.sponsor_key("Novartis Pharmaceuticals Corporation") == resolve.sponsor_key("Novartis Pharmaceuticals Corp.")
    assert resolve.sponsor_key("Merck KGaA") != resolve.sponsor_key("Merck Sharp & Dohme LLC")


def _rows():
    return [
        ("NCT1", "DRUG", "LCZ696", "Entresto|sacubitril/valsartan"),
        ("NCT2", "DRUG", "Entresto", "LCZ696"),
        ("NCT3", "DRUG", "Sacubitril/Valsartan", None),
        # one record wrongly lists Norvasc as another name for eplerenone
        ("NCT4", "DRUG", "Eplerenone", "Norvasc"),
        ("NCT5", "DRUG", "Amlodipine", "Norvasc"),
        ("NCT6", "DRUG", "Amlodipine", None),
        ("NCT7", "DRUG", "Eplerenone", "Inspra"),
        ("NCT8", "DRUG", "Eplerenone", None),
        # combination arm must not bridge its parts
        ("NCT9", "DRUG", "Furosemide + Spironolactone", "furosemide, spironolactone"),
        ("NCT10", "DRUG", "Furosemide", None),
        ("NCT11", "DRUG", "Spironolactone", None),
        ("NCT12", "DRUG", "Control group", "Furosemide"),
    ]


def _groups(rows):
    out, _ = resolve.resolve_interventions(rows, CFG)
    return {r[0]: r[2] for r in out}


def test_corroborated_alias_merges():
    g = _groups(_rows())
    assert g["LCZ696"] == g["Entresto"] == g["Sacubitril/Valsartan"]


def test_single_wrong_link_does_not_fuse_two_drugs():
    g = _groups(_rows())
    assert g["Eplerenone"] != g["Amlodipine"]


def test_combination_and_class_words_do_not_bridge():
    g = _groups(_rows())
    assert g["Furosemide"] != g["Spironolactone"]
    assert g["Control group"] != g["Furosemide"]


def test_order_independent():
    base = _groups(_rows())
    for seed in range(5):
        rows = _rows()
        random.Random(seed).shuffle(rows)
        assert _groups(rows) == base
