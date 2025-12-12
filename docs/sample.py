import polars as pl


def greet(who: str = "marimo") -> str:
    return f"Hello, {who}! Welcome to marimo-demo."


def demo_sales_data() -> pl.DataFrame:
    """Tiny helper dataset used by the marimo demo."""
    data = [
        ("West", "Coffee subscription", 120, 4.25),
        ("West", "Single-origin beans", 95, 6.50),
        ("West", "Filters", 80, 2.50),
        ("East", "Coffee subscription", 140, 4.10),
        ("East", "Single-origin beans", 110, 6.10),
        ("East", "Filters", 70, 2.30),
        ("South", "Coffee subscription", 105, 4.05),
        ("South", "Single-origin beans", 90, 6.00),
        ("South", "Filters", 60, 2.20),
    ]
    return (
        pl.DataFrame(data, schema=["region", "product", "units", "unit_price"])
        .with_columns((pl.col("units") * pl.col("unit_price")).alias("revenue"))
    )


def show_dataframe() -> pl.DataFrame:
    """Backward-compatible alias for the demo dataset."""
    return demo_sales_data()
