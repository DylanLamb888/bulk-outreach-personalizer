import tempfile
import unittest
from pathlib import Path

from bulk_enrich.focus import CommercialFocusError, CommercialFocusTable, longest_shared_phrase_words


ROOT = Path(__file__).resolve().parents[1]


class CommercialFocusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = CommercialFocusTable.load(
            ROOT / "campaigns" / "examples" / "scale-olympus-focus.csv"
        )

    def test_maps_controlled_examples_to_one_commercial_category(self) -> None:
        examples = (
            (
                "Lindeblad Piano Restoration",
                "specialism",
                "restoration, repair, refinishing, and piano sales since 1920",
                "World-class restoration, repair, refinishing, and sale of pianos since 1920",
                "piano restoration",
                "piano owners needing restoration",
            ),
            (
                "Aurora Mills Architectural Salvage",
                "product",
                "reclaimed fireplace mantels, stained glass windows, vintage cabinets & more",
                "Discover reclaimed fireplace mantels, stained glass windows, vintage cabinets & more",
                "architectural salvage",
                "architectural salvage buyers",
            ),
            (
                "Van Wyk Confections",
                "product",
                "unique and delicious confectionery candy items for fundraising",
                "Supplier to unique and delicious confectionery candy items for fundraising",
                "fundraising products",
                "organisations planning fundraisers",
            ),
            (
                "Balance Point Capital",
                "service",
                "flexible capital solutions",
                "The firm provides flexible capital solutions",
                "flexible capital solutions",
                "companies looking for flexible capital solutions",
            ),
            (
                "Coady Diemar Partners",
                "service",
                "M&A, strategic and financial advisory, and private capital market services",
                "A boutique bank providing M&A and private capital market services",
                "M&A advisory",
                "business owners considering a transaction",
            ),
            (
                "Syntera Advisors",
                "audience",
                "helping forward-thinking companies connect, collaborate, and grow with confidence",
                "Syntera helps forward-thinking companies connect, collaborate, and grow with confidence",
                "business growth",
                "companies looking to grow",
            ),
        )
        for company_name, signal_type, source_focus, evidence, focus, buyer_phrase in examples:
            with self.subTest(company=company_name):
                result = self.table.resolve(
                    company_name=company_name,
                    signal_type=signal_type,
                    source_focus=source_focus,
                    evidence=evidence,
                    max_focus_words=7,
                    max_buyer_phrase_words=8,
                )
                self.assertEqual(result.focus, focus)
                self.assertEqual(result.buyer_phrase, buyer_phrase)
                self.assertNotIn(",", result.focus)
                self.assertNotIn("since", result.focus.casefold())

    def test_generic_compression_removes_website_style_clutter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "focus.csv"
            path.write_text(
                "id,priority,pattern,signal_types,focus,buyer_phrase\n"
                "never,1,will-not-match,*,safe category,safe buyers\n",
                encoding="utf-8",
            )
            table = CommercialFocusTable.load(path)
            result = table.resolve(
                company_name="Example",
                signal_type="service",
                source_focus="innovative tax planning, bookkeeping and reporting since 1998",
                evidence="Innovative tax planning, bookkeeping and reporting since 1998",
                max_focus_words=7,
                max_buyer_phrase_words=8,
            )
            self.assertEqual(result.focus, "tax planning")
            self.assertEqual(result.buyer_phrase, "companies looking for tax planning")
            self.assertEqual(result.rule_id, "generic-compression")

    def test_generic_compression_rejects_sentence_fragments_and_slogans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "focus.csv"
            path.write_text(
                "id,priority,pattern,signal_types,focus,buyer_phrase\n"
                "never,1,will-not-match,*,safe category,safe buyers\n",
                encoding="utf-8",
            )
            table = CommercialFocusTable.load(path)
            unsafe = (
                ("Nant Global Finance", "nant Global Finance - A NantX company"),
                ("Northstar Capital", "so we understand the territory as well as anyone"),
                ("Maus Software", "attract clients"),
                ("The Capital Corporation", "an ability to offer our clients specific expertise"),
                ("Floodlight Digital", "we would love to hear more about your project"),
                ("Dickinson Brands", "for over 150 years"),
                ("United Tax", "the best tax service in the nation"),
                ("Modern Tax", "partner with Modern for expert compliance solutions"),
            )
            for company_name, source_focus in unsafe:
                with self.subTest(source_focus=source_focus):
                    with self.assertRaises(CommercialFocusError):
                        table.resolve(
                            company_name=company_name,
                            signal_type="specialism",
                            source_focus=source_focus,
                            evidence=source_focus,
                            max_focus_words=7,
                            max_buyer_phrase_words=8,
                        )

    def test_generic_compression_rejects_vague_or_awkward_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "focus.csv"
            path.write_text(
                "id,priority,pattern,signal_types,focus,buyer_phrase\n"
                "never,1,will-not-match,*,safe category,safe buyers\n",
                encoding="utf-8",
            )
            table = CommercialFocusTable.load(path)
            unsafe = (
                "practical advice and actionable steps",
                "business Owner Planning & Exit Planning",
                "business owners with market-based valuations",
            )
            for source_focus in unsafe:
                with self.subTest(source_focus=source_focus):
                    with self.assertRaises(CommercialFocusError):
                        table.resolve(
                            company_name="Example Advisors",
                            signal_type="service",
                            source_focus=source_focus,
                            evidence=source_focus,
                            max_focus_words=7,
                            max_buyer_phrase_words=8,
                        )

    def test_campaign_mapping_splits_ma_buyer_intent(self) -> None:
        examples = (
            (
                "helping owners sell their business",
                "ma-sell-side",
                "owners considering a business sale",
            ),
            (
                "buy-side M&A and acquisition origination",
                "ma-buy-side",
                "acquirers looking for suitable businesses",
            ),
            (
                "debt and equity financing and capital raising",
                "capital-raising",
                "companies looking to raise capital",
            ),
            (
                "market-based valuations for business owners",
                "business-valuation",
                "business owners needing a valuation",
            ),
            (
                "sell-side and buy-side M&A advisory",
                "ma-two-sided",
                "owners or acquirers planning a transaction",
            ),
            (
                "helping people buy or sell a business",
                "business-buy-or-sell",
                "business owners and buyers considering a transaction",
            ),
        )
        for source_focus, rule_id, buyer_phrase in examples:
            with self.subTest(source_focus=source_focus):
                result = self.table.resolve(
                    company_name="Example Advisors",
                    signal_type="service",
                    source_focus=source_focus,
                    evidence=source_focus,
                    max_focus_words=7,
                    max_buyer_phrase_words=8,
                )
                self.assertEqual(result.rule_id, rule_id)
                self.assertEqual(result.buyer_phrase, buyer_phrase)

    def test_campaign_mapping_preserves_one_useful_specificity(self) -> None:
        examples = (
            (
                "M&A advisory firm focused exclusively on wineries and vineyards",
                "wine-sector-ma",
                "winery owners considering a sale",
            ),
            (
                "M&A and growth advisory exclusively focused on IT services companies",
                "it-services-ma",
                "IT services firms considering growth or exit",
            ),
            (
                "specialized in cross-border M&A transactions especially between Europe and the United States",
                "cross-border-ma",
                "companies considering a cross-border transaction",
            ),
            (
                "off-market M&A deal origination for business acquirers",
                "off-market-origination",
                "acquirers looking for off-market opportunities",
            ),
            (
                "private equity firm focused on acquiring middle-market companies",
                "private-equity-acquisitions",
                "owners considering a private equity partner",
            ),
        )
        for evidence, rule_id, buyer_phrase in examples:
            with self.subTest(evidence=evidence):
                result = self.table.resolve(
                    company_name="Example",
                    signal_type="service",
                    source_focus=evidence,
                    evidence=evidence,
                    max_focus_words=7,
                    max_buyer_phrase_words=8,
                )
                self.assertEqual(result.rule_id, rule_id)
                self.assertEqual(result.buyer_phrase, buyer_phrase)

    def test_company_name_alone_cannot_trigger_a_mapping(self) -> None:
        with self.assertRaises(CommercialFocusError):
            self.table.resolve(
                company_name="Example M&A Advisors",
                signal_type="specialism",
                source_focus="high-touch client experience",
                evidence="A high-touch client experience",
                max_focus_words=7,
                max_buyer_phrase_words=8,
            )

    def test_other_campaign_does_not_inherit_ma_mapping(self) -> None:
        table = CommercialFocusTable.load(
            ROOT / "campaigns" / "examples" / "chapman-focus.csv"
        )
        result = table.resolve(
            company_name="Example Advisors",
            signal_type="service",
            source_focus="M&A advisory",
            evidence="M&A advisory",
            max_focus_words=7,
            max_buyer_phrase_words=8,
        )
        self.assertEqual(result.rule_id, "generic-compression")
        self.assertNotIn("raising capital", result.buyer_phrase)

    def test_generic_compression_allows_complete_commercial_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "focus.csv"
            path.write_text(
                "id,priority,pattern,signal_types,focus,buyer_phrase\n"
                "never,1,will-not-match,*,safe category,safe buyers\n",
                encoding="utf-8",
            )
            table = CommercialFocusTable.load(path)
            service = table.resolve(
                company_name="Example Advisory",
                signal_type="service",
                source_focus="tax advisory services",
                evidence="We provide tax advisory services",
                max_focus_words=7,
                max_buyer_phrase_words=8,
            )
            product = table.resolve(
                company_name="Example Manufacturing",
                signal_type="product",
                source_focus="custom CNC parts",
                evidence="We provide custom CNC parts",
                max_focus_words=7,
                max_buyer_phrase_words=8,
            )
            self.assertEqual(service.buyer_phrase, "companies looking for tax advisory services")
            self.assertEqual(product.buyer_phrase, "buyers looking for custom CNC parts")

    def test_maps_general_blind_run_categories_to_buyer_language(self) -> None:
        examples = (
            (
                "Example Restoration",
                "specialism",
                "water damage, fire repair, mold removal and carpet cleaning",
                "property restoration",
                "property owners needing restoration",
            ),
            (
                "Example Dealership Adviser",
                "specialism",
                "an automotive dealership broker offering M&A advice and valuations for auto groups",
                "automotive dealership brokerage",
                "dealership owners considering a sale",
            ),
            (
                "Example Machine Shop",
                "service",
                "state-of-the-art custom milled CNC machined parts",
                "custom CNC parts",
                "manufacturers sourcing custom CNC parts",
            ),
            (
                "Example Printer",
                "specialism",
                "a Nashville printing company",
                "commercial printing",
                "companies needing print or direct mail",
            ),
            (
                "Example Security",
                "specialism",
                "security systems offering fire alarms and card access",
                "commercial security systems",
                "businesses upgrading security or access control",
            ),
        )
        for company_name, signal_type, source_focus, focus, buyer_phrase in examples:
            with self.subTest(source_focus=source_focus):
                result = self.table.resolve(
                    company_name=company_name,
                    signal_type=signal_type,
                    source_focus=source_focus,
                    evidence=source_focus,
                    max_focus_words=7,
                    max_buyer_phrase_words=8,
                )
                self.assertEqual(result.focus, focus)
                self.assertEqual(result.buyer_phrase, buyer_phrase)

    def test_rejects_mapped_service_stacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "focus.csv"
            path.write_text(
                "id,priority,pattern,signal_types,focus,buyer_phrase\n"
                'bad,1,match,*,"tax, audit, advisory",buyers\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CommercialFocusError, "stacks multiple ideas"):
                CommercialFocusTable.load(path)

    def test_longest_shared_phrase_is_consecutive(self) -> None:
        self.assertEqual(
            longest_shared_phrase_words(
                "Could we reach companies looking for flexible capital solutions?",
                "We provide flexible capital solutions to growing firms",
            ),
            3,
        )


if __name__ == "__main__":
    unittest.main()
