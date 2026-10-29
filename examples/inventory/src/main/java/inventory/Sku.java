package inventory;

/** A stock-keeping unit code. Not editable by the change under verification. */
public record Sku(String code) {
    public Sku {
        if (code == null || code.isBlank()) throw new IllegalArgumentException("code must not be blank");
    }
}
