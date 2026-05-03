import unittest
from pathlib import Path

from edge_finder.ontology import compose, load_core
from edge_finder.typed_edges import format_typed_edge, infer_note_type, insert_typed_edge
from edge_finder.walker import Note


class TypedEdgesTest(unittest.TestCase):
    def test_insert_typed_edge_after_frontmatter(self) -> None:
        text = "---\ntitle: Sample\n---\n\n# Heading\n\nBody\n"
        updated, inserted = insert_typed_edge(text, "topic_hub", "Climate Hub")

        self.assertTrue(inserted)
        self.assertIn("topic_hub:: [[Climate Hub]]\n\n# Heading", updated)

    def test_insert_typed_edge_is_idempotent(self) -> None:
        text = "topic_hub:: [[Climate Hub]]\n\n# Heading\n"
        updated, inserted = insert_typed_edge(text, "topic_hub", "Climate Hub")

        self.assertFalse(inserted)
        self.assertEqual(text, updated)

    def test_infer_note_type_prefers_hub_membership(self) -> None:
        ontology = compose(load_core())
        note = Note(
            path=Path("Climate Hub.md"),
            relpath="Climate Hub.md",
            title="Climate Hub",
            frontmatter={},
            body="# Climate Hub\n",
        )

        inferred = infer_note_type(note, ontology, hub_paths={"Climate Hub.md"})

        self.assertEqual("hub", inferred)

    def test_format_typed_edge(self) -> None:
        self.assertEqual("co_topic:: [[Graph Theory]]", format_typed_edge("co_topic", "Graph Theory"))


if __name__ == "__main__":
    unittest.main()
