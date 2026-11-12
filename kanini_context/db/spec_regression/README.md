# Spec regression set

The 61 named tests from the schema specification (v1.5, SQL v0.9), run against the prototype's
`schema.sql` and `resolver.sql`, to show the prototype's changes keep every spec behaviour.

Run on an empty PostgreSQL 16 database:

    psql -f ../schema.sql -f seed.sql -f ../resolver.sql
    psql -f tests.sql > out.txt

`test_results.txt` is the spec's output. `test_results_prototype.txt` is the prototype's.
All 61 tests give the same verdict, including the 9 expected refusals. The differences are additions only:

| Difference | Why |
| --- | --- |
| Winning rules carry `rule_key` and `version` | Readable rule references (`rule_key@edition`) for the decision log |
| Requirement items carry `element_id`, `fact` and `unit` | Criteria are checked against registered facts; forms join items by element id |
| Refusal messages show one more column | `rule.rule_key` was added to the rule table |
| N16 lists `issued_in` | The combined links view now includes `issued_in`, `documented_by` and `replaced_by`. `documented_by` has no rows at N16 time, and `replaced_by` has none because the replacement code X0002 is not in the sample data |
