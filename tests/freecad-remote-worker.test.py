import importlib.util
import unittest
import json
import tempfile
from unittest.mock import patch
from pathlib import Path

spec = importlib.util.spec_from_file_location('worker', Path(__file__).resolve().parents[1] / 'packs/freecad/runtime/remote-worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

class RecipeTests(unittest.TestCase):
    def test_native_payload_resolves_dimensions_without_losing_editable_references(self):
        recipe = {'parameters': {'depth': 5}, 'features': [{'id': 'Block', 'op': 'box', 'length': 10, 'width': 20, 'height': 'depth'}], 'result': 'Block'}
        validated = worker.validate_input('build', {'recipe': recipe})
        self.assertEqual(validated['features'][0]['height'], 'depth')
        self.assertEqual(worker.validate_recipe(validated)['features'][0]['height'], 5)
        edited = worker.apply_changes(recipe, {'parameters': {'depth': 8}})
        self.assertEqual(edited['features'][0]['height'], 'depth')
        self.assertEqual(worker.validate_recipe(edited)['features'][0]['height'], 8)
        self.assertEqual(recipe['parameters']['depth'], 5)

    def test_absent_parameters_normalize_for_native_bridge(self):
        recipe = {'features': [{'id': 'Block', 'op': 'box', 'length': 10, 'width': 20, 'height': 5}], 'result': 'Block'}
        self.assertEqual(worker.validate_recipe(recipe)['parameters'], {})

    def test_unknown_dimension_is_rejected_before_native_process(self):
        recipe = {'features': [{'id': 'Block', 'op': 'box', 'length': 10, 'width': 20, 'height': 'unknown'}], 'result': 'Block'}
        with self.assertRaises(ValueError):
            worker.validate_recipe(recipe)

    def test_execution_report_preserves_expectations_for_canonical_verifier(self):
        recipe = {'parameters': {'depth': 5}, 'features': [{'id': 'Block', 'op': 'box', 'length': 10, 'width': 20, 'height': 'depth'}], 'result': 'Block'}
        expect = {'volume': 1000, 'bounds': [10, 20, 5], 'solids': 1}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, work = root / 'input', root / 'work'
            source.mkdir(); work.mkdir()
            (work / 'request.json').write_text(json.dumps({'toolId': 'cad.freecad.build', 'arguments': {'recipe': recipe, 'expect': expect}}))
            with patch.object(worker.sys, 'argv', ['worker', str(source), str(work)]), patch.object(worker, 'run_native', return_value=True) as native:
                self.assertEqual(worker.main(), 0)
            self.assertEqual(json.loads((work / 'report.json').read_text())['expect'], expect)
            self.assertEqual(native.call_args_list[0].args[2]['recipe']['features'][0]['height'], 5)
            self.assertEqual(json.loads((work / 'model.recipe.json').read_text())['features'][0]['height'], 'depth')

if __name__ == '__main__':
    unittest.main()
