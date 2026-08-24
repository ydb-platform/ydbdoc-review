## Rules for configuring metadata distribution subsystems {#metadata-subsystems-reconfig-rules}

1. To change the configuration without cluster unavailability, add and remove ring groups.
2. The transition to the new configuration is performed in 4 sequential steps.

   Newly created or ready-to-remove ring groups are marked with the `WriteOnly: true` flag.

   Therefore, pause for at least `1 minute` between steps.

   - Add a new ring group with the `WriteOnly: true` parameter.
   - Remove the `WriteOnly` flag.
   - Set the `WriteOnly: true` flag on the old ring group.
   - Remove the old ring group.

## Example

