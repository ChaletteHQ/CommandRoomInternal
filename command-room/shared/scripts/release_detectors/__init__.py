# Release detectors — one module per release that introduces post-update
# remediations. Each module exports detector functions that return either:
#   {"applies": False}
#   {"applies": True, "context": {...key/value pairs for prompt template...}}
#
# Detectors are pure read functions over the user's _hq/data files. They MUST
# NOT mutate workspace state — the update-bridge skill calls the detector to
# decide whether to surface a remediation prompt, then mutates only on user
# confirmation.
