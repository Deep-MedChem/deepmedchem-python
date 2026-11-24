from deepmedchem import SearchResult, combine_results


def result(database_id, *rows):
    return SearchResult.model_validate(
        {"database_id": database_id, "metric": "ECFP4 Tanimoto", "results": list(rows)}
    )


ASPIRIN_A = {
    "smiles": "CC(=O)Oc1ccccc1C(=O)O",
    "score": 0.9,
    "product_id": "a1",
    "catalog_id": "A-1",
    "structure_id": "s-aspirin",
    "also_in": [
        {
            "database_id": "vendor-c",
            "database_release": "1",
            "catalog_id": "C-9",
            "catalog_ids": ["C-9"],
            "price": 40,
        },
        {
            "database_id": "vendor-b",
            "database_release": "1",
            "catalog_id": "B-7",
            "catalog_ids": ["B-7"],
        },
    ],
}
ASPIRIN_B = {
    "smiles": "CC(=O)Oc1ccccc1C(=O)O",
    "score": 0.8,
    "product_id": "b7",
    "catalog_id": "B-7",
    "structure_id": "s-aspirin",
    "also_in": [],
}
ETHANOL_B = {
    "smiles": "CCO",
    "score": 0.95,
    "product_id": "b2",
    "catalog_id": "B-2",
    "structure_id": "s-ethanol",
    "also_in": [],
}


def test_hits_carry_typed_identity_and_other_catalogues():
    hit = result("vendor-a", ASPIRIN_A).hits[0]
    assert hit.structure_id == "s-aspirin"
    assert [(item.database_id, item.catalog_id, item.price) for item in hit.also_in] == [
        ("vendor-c", "C-9", 40),
        ("vendor-b", "B-7", None),
    ]
    assert "structure_id" not in hit.extra and "also_in" not in hit.extra
    # A site that did not look (or a hosted search) sends neither field.
    plain = result("vendor-a", {"smiles": "CCO", "score": 0.5}).hits[0]
    assert plain.structure_id is None and plain.also_in is None


def test_each_structure_once_with_the_cheapest_source_first():
    order = ["vendor-a", "vendor-b", "vendor-c"]
    combined = combine_results(
        [result("vendor-b", ASPIRIN_B, ETHANOL_B), result("vendor-a", ASPIRIN_A)],
        library_order=order,
    )
    assert [row.structure_id for row in combined] == ["s-ethanol", "s-aspirin"]  # best score first
    aspirin = combined[1]
    # vendor-c was not searched but is the only priced source; the rest keep the library order.
    assert [(s.database_id, s.catalog_id, s.price) for s in aspirin.sources] == [
        ("vendor-c", "C-9", 40),
        ("vendor-a", "A-1", None),
        ("vendor-b", "B-7", None),
    ]
    assert (aspirin.database_id, aspirin.catalog_id, aspirin.price, aspirin.score) == (
        "vendor-c",
        "C-9",
        40,
        0.9,
    )


def test_without_prices_the_first_library_wins_and_unidentified_hits_stay_separate():
    unpriced = {**ASPIRIN_A, "also_in": [{"database_id": "vendor-b", "catalog_id": "B-7"}]}
    combined = combine_results(
        [result("vendor-b", ASPIRIN_B), result("vendor-a", unpriced)],
        library_order=["vendor-a", "vendor-b"],
    )
    assert combined[0].database_id == "vendor-a"
    # Without library_order the order of the results decides.
    assert (
        combine_results([result("vendor-b", ASPIRIN_B), result("vendor-a", unpriced)])[
            0
        ].database_id
        == "vendor-b"
    )
    same = {"smiles": "CCO", "product_id": "x"}
    assert len(combine_results([result("vendor-a", same), result("vendor-b", same)])) == 2
