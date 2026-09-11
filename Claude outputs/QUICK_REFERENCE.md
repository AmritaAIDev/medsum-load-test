# Quick Reference - Unified SOAP/Medication API

## Import Everything You Need

```python
from medsum_testing.backend.services.soap_comparator import (
    # Main entry points
    compare_soap,
    compare_soap_three_way,
    validate_medications,
    compare_medications,  # Alias for validate_medications
    
    # Medication comparison classes
    MedicationNormalizer,
    MedicationMatcher,
    MedicationComparator,
    ScoringComparator,
    ValidationComparator,
    
    # Legacy wrappers (if still using old names)
    score_soap_legacy,
)

from medsum_testing.backend.services.soap_prompts import (
    MEDICAL_COMPARISON_PROMPT,
    SOAP_COMPARISON_PROMPT,
    MEDICATION_COMPARISON_PROMPT,
    TRANSLATION_COMPARISON_PROMPT,
    REGRESSION_COMPARISON_PROMPT,
    SUMMARY_COMPARISON_PROMPT,
)
```

---

## One-Liners

### Compare two SOAP notes
```python
result = compare_soap(ground_truth_dict, generated_dict)
print(f"Similarity: {result['similarity_score']}")
```

### Three-way comparison
```python
result = compare_soap_three_way(gt, gen, raw)
print(f"GT vs Gen: {result['scores']['gt_vs_generated']}")
print(f"GT vs Raw: {result['scores']['gt_vs_raw']}")
print(f"Raw vs Gen: {result['scores']['raw_vs_generated']}")
```

### Validate medications
```python
result = validate_medications(transcription_result)
print(f"Added: {len(result['added_medicines'])}")
print(f"Removed: {len(result['removed_medicines'])}")
print(f"Changed: {len(result['changed_medicines'])}")
```

### Match medications (order-independent)
```python
matcher = ScoringComparator(fuzzy_threshold=0.85)
result = matcher.compare(ground_truth_meds, generated_meds)
print(f"Accuracy: {result['accuracy'] * 100:.1f}%")
```

---

## Testing

### Run all unified API tests
```bash
pytest tests/test_soap_comparator.py -v
```

### Run specific test class
```bash
pytest tests/test_soap_comparator.py::TestCompareSoapUnified -v
pytest tests/test_soap_comparator.py::TestValidateMedications -v
```

---

## Common Patterns

### Pattern 1: Compare and log severity
```python
result = compare_soap(gt, gen)
if result['overall_severity'] == 'none':
    print("✅ Perfect match")
elif result['overall_severity'] == 'low':
    print("✅ Minor differences only")
else:
    print("❌ Significant differences")
    print(result['summary'])
```

### Pattern 2: Analyze section accuracy
```python
result = compare_soap(gt, gen)
sections = result['section_details']
for section, details in sections.items():
    print(f"{section:12} {details['score']:3.0f}%")
```

### Pattern 3: Track medication changes
```python
result = validate_medications(transcription)
for med in result['changed_medicines']:
    print(f"Changed: {med['drug_name']}")
```

---

## Configuration

### Medication fuzzy matching thresholds
```python
# Scoring (more lenient) - 0.85
scorer = ScoringComparator(fuzzy_threshold=0.85)

# Validation (stricter) - 0.90
validator = ValidationComparator(fuzzy_threshold=0.90)
```

### Enable LLM section_details overlay
```python
result = compare_soap(
    ground_truth,
    generated,
    use_llm=True,  # Enable LLM for better NA/Missing classification
    model="gpt-4o-mini"
)
```

---

## Migration from Old API

### OLD (deprecated but still works)
```python
from medsum_testing.backend.services.soap_fact_scorer import score_soap
result = score_soap(gt, gen)
```

### NEW (recommended)
```python
from medsum_testing.backend.services.soap_comparator import compare_soap
result = compare_soap(gt, gen)
```

---

## Further Reading

- **DEPLOYMENT_SUMMARY.md** - Full details of what was deployed
- **INTEGRATION_GUIDE.md** - Detailed migration steps
- **test_soap_comparator.py** - 30+ comprehensive test examples

*API Version: 1.0 | Last updated: 2026-09-11*
