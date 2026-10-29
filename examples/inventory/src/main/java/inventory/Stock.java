package inventory;

public class Stock {
    private int available;

    public Stock(int available) {
        if (available < 0) throw new IllegalArgumentException("available must be >= 0");
        this.available = available;
    }

    /** Reserves qty units and returns how many remain. */
    public int reserve(int qty) {
        available -= qty;
        return available;
    }

    public int available() {
        return available;
    }
}
