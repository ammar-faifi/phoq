"""
Simplified mode-pairing QKD simulation (Zeng, Zhou, Wu & Ma, Nat. Commun. 13, 3903, 2022).

Implements Box 1 (without decoy states) and Box 2 (simple pairing strategy):
  1. Alice & Bob prepare phase-randomised weak coherent pulses
  2. Pulses travel through lossy fibre to Charlie, with relative laser phase drift
  3. Charlie interferes them on a 50:50 beam splitter and announces L / R clicks
  4. Alice & Bob pair clicked rounds (max interval l), sift bases, map keys
  5. Report pairing rate (vs. Eq. 4), Z-basis QBER, X-basis error rate, raw key

No decoy states => no estimate of q_(1,1) or e^X_(1,1), so no secure key rate here. The party
version of the showcase (alice.py / bob.py / station.py) adds the decoy intensity ν, error
correction, verification and privacy amplification: see keyrate.py and postprocess.py.
"""

from dataclasses import dataclass
import numpy as np

# Basis labels
B_ZERO, B_Z, B_X = 0, 1, 2
# Detector labels
DET_L, DET_R = 0, 1


@dataclass
class Params:
    N: int = 5_000_000  # number of rounds
    distance_km: float = 100.0  # total Alice–Bob distance (Charlie in the middle)
    loss_db_per_km: float = 0.2  # fibre loss
    mu: float = 0.4  # signal intensity
    p_mu: float = 0.5  # probability of sending mu (else vacuum)
    D: int = 16  # number of discrete phases
    eta_d: float = 0.5  # detector efficiency
    p_dark: float = 1e-8  # dark-count probability per detector per round
    linewidth_hz: float = 3e3  # laser linewidth (each laser)
    rep_rate_hz: float = 625e6  # system repetition rate
    seed: int = 2022


# ---------------------------------------------------------------------------
# Step 1: state preparation
# ---------------------------------------------------------------------------
def prepare(rng, N, mu, p_mu, D):
    """Return per-round intensity (0 or mu) and discrete phase index k (phase 2*pi*k/D)."""
    intensity = np.where(rng.random(N) < p_mu, mu, 0.0)
    phase_idx = rng.integers(0, D, size=N)
    return intensity, phase_idx


# ---------------------------------------------------------------------------
# Channel: loss + relative phase drift
# ---------------------------------------------------------------------------
def arm_transmittance(p: Params):
    """Transmittance of one arm (Alice->Charlie or Bob->Charlie)."""
    return 10 ** (-p.loss_db_per_km * (p.distance_km / 2) / 10)


def send(rng, p: Params, int_a, ph_a, int_b, ph_b):
    """
    Coherent states stay coherent through a pure-loss channel: amplitudes scale by sqrt(eta_s).
    The two independent lasers' relative phase performs a random walk (phase diffusion);
    we put all of it on Bob's arriving pulse.
    """
    eta_s = arm_transmittance(p)
    sigma = np.sqrt(2 * np.pi * (2 * p.linewidth_hz) / p.rep_rate_hz)  # per-pulse step
    drift = np.cumsum(rng.normal(0.0, sigma, size=p.N))

    phi_a = 2 * np.pi * ph_a / p.D
    phi_b = 2 * np.pi * ph_b / p.D + drift
    alpha = np.sqrt(eta_s * int_a) * np.exp(1j * phi_a)
    beta = np.sqrt(eta_s * int_b) * np.exp(1j * phi_b)
    return alpha, beta, eta_s


# ---------------------------------------------------------------------------
# Step 2: Charlie's single-photon interference measurement
# ---------------------------------------------------------------------------
def charlie_measure(rng, alpha, beta, eta_d, p_dark):
    """
    50:50 BS: L <- (alpha+beta)/sqrt2, R <- (alpha-beta)/sqrt2 (still coherent states).
    Threshold detector click prob: 1 - (1-p_dark) * exp(-eta_d * |amp|^2), independent for L and R.
    Returns C (success = exactly one click) and which detector clicked.
    """
    nL = np.abs((alpha + beta) / np.sqrt(2)) ** 2
    nR = np.abs((alpha - beta) / np.sqrt(2)) ** 2
    clickL = rng.random(alpha.size) < 1 - (1 - p_dark) * np.exp(-eta_d * nL)
    clickR = rng.random(alpha.size) < 1 - (1 - p_dark) * np.exp(-eta_d * nR)
    C = clickL ^ clickR  # exactly one click
    detector = np.where(clickL, DET_L, DET_R)
    # [CLASSICAL #1] Charlie -> Alice & Bob: PUBLIC announcement of C_i and L/R for every round.
    #   Assumed authenticated, error-free and instantaneous. Returning (C, detector) to a caller
    #   that then hands them to both parties is the simulated broadcast.
    #   Also assumes a shared round clock (index i means the same pulse for everyone).
    return C, detector, clickL & clickR


# ---------------------------------------------------------------------------
# Step 3: simple pairing strategy (Box 2)
# ---------------------------------------------------------------------------
def simple_pairing(click_idx, l):
    """
    Equivalent to Box 2 but iterating over clicked rounds only.
    (Box 2 line 10 should read  i - F_k >= l ; the printed 'F_k - i' is a sign typo.)
    Pair the front click with the next click if it is within l rounds;
    otherwise that next click becomes the new front.

    [CLASSICAL #2] Pairing needs only Charlie's public C_i (input click_idx), so Alice and Bob
    can each run this deterministic algorithm locally and get identical (F, R) with no extra
    messages. Calling it ONCE and giving the same F, R to both is equivalent to that, or to one
    party announcing the pairing publicly. Both must agree on l in advance (public parameter).
    """
    fronts, rears = [], []
    front = None
    for i in click_idx:
        if front is None:
            front = i
        elif i - front <= l:
            fronts.append(front)
            rears.append(i)
            front = None
        else:
            front = i
    return np.array(fronts, dtype=np.int64), np.array(rears, dtype=np.int64)


def pairing_rate_theory(p, l):
    """Eq. (4)."""
    return 1.0 / (1.0 / (p * (1 - (1 - p) ** l)) + 1.0 / p)


# ---------------------------------------------------------------------------
# Steps 4 & 5: basis sifting and key mapping
# ---------------------------------------------------------------------------
def assign_basis(int_i, int_j):
    """(0,mu)/(mu,0) -> Z ; (mu,mu) -> X ; (0,0) -> '0'."""
    nz_i, nz_j = int_i > 0, int_j > 0
    basis = np.full(int_i.size, B_ZERO)
    basis[nz_i ^ nz_j] = B_Z
    basis[nz_i & nz_j] = B_X
    return basis


def sift_and_map(F, R, int_a, ph_a, int_b, ph_b, detector, D):
    # Local: each party computes its OWN basis from its own private intensities.
    basis_a = assign_basis(int_a[F], int_a[R])
    basis_b = assign_basis(int_b[F], int_b[R])

    # ---- Z-pairs ----
    # [CLASSICAL #3] Basis announcement (Box 1, Step 4): Alice & Bob publicly exchange the
    #   per-pair basis label (Z / X / '0'), NOT the intensities' order. Comparing basis_a with
    #   basis_b in one function is the simulated exchange; only the labels are "sent".
    z = (basis_a == B_Z) & (basis_b == B_Z)
    ka_z = (int_a[F][z] > 0).astype(int)  # Alice: (0,mu)->0, (mu,0)->1
    kb_z = (int_b[F][z] == 0).astype(int)  # Bob:   (mu,0)->0, (0,mu)->1

    # ---- X-pairs ----
    x = (basis_a == B_X) & (basis_b == B_X)
    half = D // 2
    da = (ph_a[R][x] - ph_a[F][x]) % D  # relative phase index
    db = (ph_b[R][x] - ph_b[F][x]) % D
    ka_x, theta_a = da // half, da % half
    kb_x, theta_b = db // half, db % half
    # Bob uses Charlie's already-public L/R record ([CLASSICAL #1]); no new message needed.
    flip = detector[F][x] != detector[R][x]  # (L,R) or (R,L) -> Bob flips
    kb_x = kb_x ^ flip.astype(int)
    # [CLASSICAL #4] For X-pairs, Alice & Bob publicly announce their alignment angle theta
    #   and keep the pair only if theta_a == theta_b. The key bit (ka_x / kb_x) stays private.
    keep = theta_a == theta_b  # alignment-angle sifting
    ka_x, kb_x = ka_x[keep], kb_x[keep]

    return dict(
        basis_a=basis_a,
        basis_b=basis_b,
        ka_z=ka_z,
        kb_z=kb_z,
        ka_x=ka_x,
        kb_x=kb_x,
        n_pairs=F.size,
        n_z=int(z.sum()),
        n_x_before_theta=int(x.sum()),
        n_x=int(keep.sum()),
    )


# ---------------------------------------------------------------------------
# Pretty printing for the demo
# ---------------------------------------------------------------------------
def fmt_int(v, mu):
    return "mu" if v > 0 else " 0"


def show_demo(int_a, ph_a, int_b, ph_b, detector, click_idx, F, R, res, mu, D, n=8):
    print("\n--- First clicked rounds (what Charlie announces) ---")
    print(f"{'round':>9} | {'Alice (I, k)':>13} | {'Bob (I, k)':>11} | det")
    for i in click_idx[:n]:
        print(
            f"{i:>9} | {fmt_int(int_a[i], mu):>6}, {ph_a[i]:>4} | "
            f"{fmt_int(int_b[i], mu):>4}, {ph_b[i]:>4} | {'L' if detector[i] == DET_L else 'R'}"
        )

    names = {B_ZERO: "'0'", B_Z: "Z", B_X: "X"}
    print("\n--- First pairs (front, rear) and bases ---")
    print(f"{'front':>9} {'rear':>9} | Alice | Bob | kept?")
    for k in range(min(n, F.size)):
        ba, bb = res["basis_a"][k], res["basis_b"][k]
        kept = "yes" if ba == bb and ba in (B_Z, B_X) else "no"
        print(f"{F[k]:>9} {R[k]:>9} | {names[ba]:>5} | {names[bb]:>3} | {kept}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(
    p: Params, l_values=(1, 10, 100, 1000, 10_000, 100_000), demo_l=1000, plot=True
):
    rng = np.random.default_rng(p.seed)

    # Step 1: preparation
    int_a, ph_a = prepare(rng, p.N, p.mu, p.p_mu, p.D)
    int_b, ph_b = prepare(rng, p.N, p.mu, p.p_mu, p.D)

    # Sending + Step 2: measurement
    alpha, beta, eta_s = send(rng, p, int_a, ph_a, int_b, ph_b)
    C, detector, double = charlie_measure(rng, alpha, beta, p.eta_d, p.p_dark)
    click_idx = np.flatnonzero(C)
    p_click = click_idx.size / p.N

    print(
        f"Distance {p.distance_km:.0f} km | eta_s = {eta_s:.3e} | total eta = {eta_s**2:.3e}"
    )
    print(
        f"Rounds N = {p.N:,} | single clicks = {click_idx.size:,} (p = {p_click:.3e}) "
        f"| double clicks discarded = {int(double.sum()):,}"
    )

    # Demo printout at one l
    F, R = simple_pairing(click_idx, demo_l)
    res = sift_and_map(F, R, int_a, ph_a, int_b, ph_b, detector, p.D)
    show_demo(int_a, ph_a, int_b, ph_b, detector, click_idx, F, R, res, p.mu, p.D)

    # Steps 3-5 for several l: physics is fixed, only post-processing changes
    print("\n--- Post-processing for different maximal pairing intervals l ---")
    print(
        f"{'l':>8} | {'r_p sim':>10} | {'r_p Eq.4':>10} | {'#Z':>7} | {'E_Z':>7} | "
        f"{'#X':>6} | {'E_X':>6} | {'raw Z bits/pulse':>16}"
    )
    rows = []
    for l in l_values:
        F, R = simple_pairing(click_idx, l)
        res = sift_and_map(F, R, int_a, ph_a, int_b, ph_b, detector, p.D)
        rp_sim = F.size / p.N
        rp_th = pairing_rate_theory(p_click, l)
        # [CLASSICAL #5] Parameter estimation. In a real run Alice & Bob CANNOT compare keys
        #   directly. E_X: they publicly announce their X-pair bits (X-pairs are used for
        #   estimation, not key). E_Z: learned from a disclosed sample or from error correction.
        #   Here the simulator compares full key strings with a "god's-eye" view, which equals
        #   announcing everything; such announced bits would then be discarded from the key.
        ez = np.mean(res["ka_z"] != res["kb_z"]) if res["n_z"] else np.nan
        ex = np.mean(res["ka_x"] != res["kb_x"]) if res["n_x"] else np.nan
        # [CLASSICAL #6] Error correction (leaks ~ f*H(E_Z) bits per Z bit) and privacy
        #   amplification (shared hash seed) both use the public channel. Not done in this
        #   single-process reference; the parties in station.py run Cascade on chunks of sifted
        #   bits (ec-start / ec-query / ec-parity / ec-done), verify with a universal-hash tag
        #   (verified) and hash with a Toeplitz matrix sized by the MP-QKD key-rate formula (pa);
        #   keyrate.py has the decoy bounds and postprocess.py the primitives.
        raw = res["n_z"] / p.N
        rows.append((l, rp_sim, rp_th, res["n_z"], ez, res["n_x"], ex, raw))
        print(
            f"{l:>8} | {rp_sim:>10.3e} | {rp_th:>10.3e} | {res['n_z']:>7} | {ez:>7.4f} | "
            f"{res['n_x']:>6} | {ex:>6.3f} | {raw:>16.3e}"
        )

    if plot:
        make_plot(rows, p, p_click)
    return rows


def make_plot(rows, p, p_click, fname="mpqkd_sim.png"):
    import matplotlib.pyplot as plt

    plt.style.use("dark_background")
    rows = np.array(rows, dtype=float)
    l, rp_sim, rp_th, _, ez, _, ex, _ = rows.T
    l_fine = np.logspace(0, np.log10(l.max()), 200)

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    ax[0].loglog(l_fine, pairing_rate_theory(p_click, l_fine), "-", label="Eq. (4)")
    ax[0].loglog(l, rp_sim, "o", label="simulation")
    ax[0].axvline(1 / p_click, ls=":", c="gray", label="l = 1/p")
    ax[0].set(
        xlabel="maximal pairing interval l",
        ylabel="pairs per pulse $r_p$",
        title=f"Pairing rate, {p.distance_km:.0f} km",
    )
    ax[0].legend()

    ax[1].semilogx(l, ez, "s-", label="Z-basis QBER $E_Z$")
    ax[1].semilogx(l, ex, "o-", label="X-basis error rate")
    ax[1].set(
        xlabel="maximal pairing interval l",
        ylabel="error rate",
        title="Error rates vs. l",
    )
    ax[1].legend()
    fig.tight_layout()
    fig.savefig(fname, dpi=150)
    print(f"\nSaved plot to {fname}")


if __name__ == "__main__":
    run(Params())
