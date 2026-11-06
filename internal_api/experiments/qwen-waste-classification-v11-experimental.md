# Qwen3-VL compact classification probe

Version: `qwen-waste-classification-v11-experimental`

This candidate is isolated for controlled experiments. It is not the
production prompt and has not been selected for deployment.

## Candidate prompt

```text
Classify the main visible waste item. Return one JSON object only, with exactly
these fields: object_name_th, waste_category, material_subtype,
visible_condition, confidence, has_food_scraps, has_liquid. Do not return
candidates, explanations, or bin/disposal choices.

Allowed waste_category: general, pet_bottle, plastic, paper, glass, metal,
ewaste, fuel, hazardous, infectious, unknown.
Allowed material_subtype: pet_bottle, stretch_plastic, rigid_plastic,
plastic_straw, plastic_spoon, multilayer_packaging, snack_sachet,
coffee_sachet, refill_pouch, non_recyclable_foam, plastic_coated_paper,
non_recyclable_dry_plastic, paper, cardboard, glass, metal, e_waste,
hazardous, infectious, general, unknown.
Allowed visible_condition: clean_dry, food_or_liquid_contaminated, unknown.

Use ewaste/e_waste and name an identifiable smartphone โทรศัพท์มือถือ when
phone-specific features are visible, such as a screen, rear camera module,
phone-shaped case, buttons, or ports. A rear-facing phone in a case can still
be identified from its camera module or phone form. A generic rectangle is not
enough evidence.

Use pet_bottle/pet_bottle only when a disposable beverage bottle is supported
by visible body construction; shape alone is insufficient. If the object,
material, or destination is uncertain, use unknown/unknown. Set
visible_condition to clean_dry only when visibly clean and dry, contaminated
only for visible food/liquid residue, otherwise unknown. Set the two boolean
fields only from visible evidence. Confidence must be a number from 0 to 1.

Return compact valid JSON and no other text.
```

## Probe set

Exactly three read-only image requests are planned, each with `qwen3-vl:4b`,
temperature 0, `max_tokens: 256`, JSON response format, and a 40-second
request timeout:

- Rear-facing phone: stored request `949e0c04-2886-46a7-9904-d3858028e1af`.
- Clear-bodied PET beverage bottle: stored image
  `06787066-235e-4181-951e-918c6ff09e07.jpg`.
- Clear glass bottle: stored image `bb2a000d-5c2b-41a0-b932-ec73f916937f.jpg`.

The prompt omits top-three candidates. This reduces response and reasoning
work, but loses alternative-category visibility, confidence-ranked ambiguity
signals, candidate-based review flags, and the ability to inspect plausible
near misses. A lightweight optional review alternative is to log the raw
single primary result with prompt version and image ID, then let a reviewer
mark `needs_second_opinion` in Scan History when the primary confidence is low
or disagrees with the visible object. Do not infer alternatives in the model
client or change production review behavior as part of this experiment.

## Controlled probe results

Exactly three requests were sent to `qwen3-vl:4b`, with temperature 0,
`max_tokens: 256`, JSON response format, and 40-second per-request timeouts.
All completed with HTTP 200 and `finish_reason: stop`, but `message.content` was
empty and reasoning used the full 256 completion tokens. JSON validity,
normalized classification, and visual plausibility are therefore unavailable
for every probe.

| Probe | Elapsed | Content length | Reasoning length | JSON | Normalized result | Plausible correctness |
| --- | ---: | ---: | ---: | --- | --- | --- |
| Rear-facing phone | 2.42 s | 0 | 1,176 | Invalid/empty | Unavailable | Not assessable |
| PET beverage bottle | 3.22 s | 0 | 1,103 | Invalid/empty | Unavailable | Not assessable |
| Glass bottle | 3.23 s | 0 | 1,087 | Invalid/empty | Unavailable | Not assessable |

Each request reported usage of 1,464 prompt tokens, 256 completion tokens, and
1,720 total tokens. The experimental prompt did not produce usable
classifications. Do not select it for production or switch the live model.
