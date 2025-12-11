import marimo as mo
import polars as pl

from {{ package_name }} import demo_sales_data, greet


@mo.app
def app():
    """Tiny marimo app exported to self-contained HTML."""
    sales = demo_sales_data()
    max_revenue = int(sales.get_column("revenue").max())

    region = mo.ui.dropdown(
        label="Region",
        options=["All"] + sorted(sales.get_column("region").unique().to_list()),
        value="All",
    )
    min_revenue = mo.ui.slider(
        start=0,
        stop=max_revenue,
        step=25,
        value=max_revenue // 3,
        label="Minimum revenue",
    )
    top_n = mo.ui.slider(
        start=3,
        stop=10,
        value=5,
        step=1,
        label="Rows to show",
    )

    filtered = sales
    if region.value != "All":
        filtered = filtered.filter(pl.col("region") == region.value)
    filtered = (
        filtered.filter(pl.col("revenue") >= min_revenue.value)
        .sort("revenue", descending=True)
        .head(top_n.value)
    )

    row_count = filtered.height
    avg_revenue = float(filtered.get_column("revenue").mean()) if row_count else 0.0
    total_units = int(filtered.get_column("units").sum()) if row_count else 0

    return mo.vstack(
        [
            mo.md(f"{greet()} Use the controls below to explore the sample data."),
            mo.hstack([region, min_revenue, top_n], wrap=True, gap="0.75rem"),
            mo.md(
                f"Showing **{row_count}** rows · "
                f"Average revenue **${avg_revenue:.2f}** · "
                f"Total units **{total_units}**"
            ),
            mo.ui.table(filtered.to_dict(as_series=False)),
        ],
        gap="0.75rem",
    )


if __name__ == "__main__":
    app.run()
