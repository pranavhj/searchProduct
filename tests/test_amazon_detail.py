"""Amazon product-page parsing for vetting evidence."""
from __future__ import annotations

from price_watch import amazon_detail

# Shape of the live widget (2026-10-07): every row repeats all five star labels in its visible text;
# only the aria-label pairs a star with its percentage.
HISTOGRAM = """<ul id="histogramTable">
<li><a aria-label="69 percent of reviews have 5 stars"><span>5 star</span><span>4 star</span>5 star</a></li>
<li><a aria-label="11 percent of reviews have 4 stars"><span>5 star</span><span>4 star</span>4 star</a></li>
<li><a aria-label="7 percent of reviews have 1 stars"><span>5 star</span><span>1 star</span>1 star</a></li>
</ul>"""


def test_histogram_pairs_each_star_with_its_own_percentage() -> None:
    assert amazon_detail.parse_detail(HISTOGRAM)["star_histogram"] == "5★ 69% 4★ 11% 1★ 7%"
