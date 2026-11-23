// Renderer, camera, lights, room, the optical tables and their instrument shelves.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { TABLE, TABLES, GRID, SHELF, setTableDims, rowSpan } from './util.js';
import { makeBreadboardTexture } from './textures.js';

export const HOME_VIEW = { pos: new THREE.Vector3(0, 122, 176), target: new THREE.Vector3(0, 9, -6) };
const BASE = { w: 150, d: 90 };   // the table size HOME_VIEW was framed for

export function createScene(container) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(container.clientWidth, container.clientHeight);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.domElement.className = 'three';
  renderer.domElement.tabIndex = 0;
  container.prepend(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x06080c);
  scene.fog = new THREE.Fog(0x06080c, 320, 720);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.42;

  const camera = new THREE.PerspectiveCamera(40, container.clientWidth / container.clientHeight, 1, 2500);
  camera.position.copy(HOME_VIEW.pos);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(HOME_VIEW.target);
  controls.enableDamping = true;
  controls.dampingFactor = 0.09;
  controls.screenSpacePanning = false;
  controls.maxPolarAngle = Math.PI * 0.46;
  controls.minDistance = 20;
  controls.maxDistance = 420;
  controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };

  const { key, accents } = addLights(scene);
  const wall = addRoom(scene);
  const boardTex = makeBreadboardTexture(renderer);
  let built = [];
  let home = { s: 1, sc: 1 };

  /** Rebuild the tables and shelves at a new size and fit the camera, shadows and fog to the row. */
  function setTableSize(w, d) {
    const dims = setTableDims(w, d);
    for (const g of built) { g.removeFromParent(); disposeGroup(g); }
    built = TABLES.flatMap((t) => {
      const table = buildTable(boardTex), shelf = buildShelf();
      table.position.x = shelf.position.x = t.x;
      return [table, shelf];
    });
    scene.add(...built);
    const row = rowSpan().width;
    const s = Math.max(TABLE.width / BASE.w, TABLE.depth / BASE.d);
    home = { s, sc: Math.max(s, 0.85) };   // small tables: keep the shelf in frame
    fitView();
    key.position.set(70 * s, 170 * Math.max(1, s * 0.8), 90 * s);
    Object.assign(key.shadow.camera, {
      left: -(row / 2 + 40), right: row / 2 + 40,
      top: TABLE.depth / 2 + 50, bottom: -(TABLE.depth / 2 + 50), far: 450 * Math.max(1, s),
    });
    key.shadow.camera.updateProjectionMatrix();
    // Accent lights stay outside the tables' footprint so they never glare on a breadboard.
    accents[0].position.set(-(row / 2 + 65), 60, -(TABLE.depth / 2 + 75));
    accents[1].position.set(row / 2 + 75, 40, -(TABLE.depth / 2 + 55));
    return dims;
  }

  /**
   * The home view for the viewport's current shape: framed on one table, then, for a row of
   * tables, backed off along the same line of sight until the whole row fits across. The zoom-out
   * limit, far plane and fog follow the distance.
   */
  function fitView() {
    const { s, sc } = home;
    HOME_VIEW.pos.set(0, 9 + 113 * sc, -6 + 182 * sc);
    HOME_VIEW.target.set(0, 9, -6 * sc);
    let k = s;
    if (TABLES.length > 1) {
      const row = rowSpan();
      const ends = [row.xMin, row.xMax].flatMap((x) => [new THREE.Vector3(x, 0, TABLE.depth / 2), new THREE.Vector3(x, SHELF.y + 25, SHELF.z)]);
      const cam = camera.clone();
      const back = new THREE.Vector3().subVectors(HOME_VIEW.pos, HOME_VIEW.target);
      const fits = () => {
        cam.position.copy(HOME_VIEW.target).add(back);
        cam.lookAt(HOME_VIEW.target);
        cam.updateMatrixWorld();
        return ends.every((e) => Math.abs(e.clone().project(cam).x) < 0.9);   // clear of the toolbox tab
      };
      let grow = 1;
      while (!fits() && grow < 8) { back.multiplyScalar(1.03); grow *= 1.03; }
      HOME_VIEW.pos.copy(HOME_VIEW.target).add(back);
      k = s * grow;
    }
    // the back wall grows with a row, so a camera this far out never sees its edges
    const wide = TABLES.length > 1 ? Math.max(1, (rowSpan().width + 800) / 900) : 1;
    wall.scale.set(wide, Math.max(1, wide * 0.8), 1);
    wall.position.y = -(TABLE.thickness + TABLE.legHeight) + 160 * wall.scale.y;
    controls.maxDistance = 420 * k;
    camera.far = 2500 * Math.max(1, k);
    camera.updateProjectionMatrix();
    scene.fog.near = 320 * k;
    scene.fog.far = 720 * k;
  }
  setTableSize(TABLE.width, TABLE.depth);

  return { renderer, scene, camera, controls, setTableSize, fitView };
}

function disposeGroup(g) {
  g.traverse((o) => {
    if (!o.isMesh) return;
    o.geometry.dispose();
    o.material.map?.dispose();                     // a per-table clone of the breadboard texture
    o.material.dispose();
  });
}

function addLights(scene) {
  scene.add(new THREE.HemisphereLight(0x9fb8ff, 0x0b0d12, 0.55));

  const key = new THREE.DirectionalLight(0xffffff, 2.1);
  key.position.set(70, 170, 90);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, { left: -115, right: 115, top: 95, bottom: -95, near: 20, far: 450 });
  key.shadow.bias = -0.0004;
  key.shadow.normalBias = 0.04;
  scene.add(key, key.target);

  const fill = new THREE.DirectionalLight(0x7aa7ff, 0.45);
  fill.position.set(-120, 80, -40);
  scene.add(fill);

  // Colored accent lights give the dark lab some depth.
  const cyan = new THREE.PointLight(0x22d3ee, 0.35, 0, 0);
  cyan.position.set(-140, 60, -120);
  const magenta = new THREE.PointLight(0xff3fb4, 0.22, 0, 0);
  magenta.position.set(150, 40, -100);
  scene.add(cyan, magenta);
  return { key, accents: [cyan, magenta] };
}

function addRoom(scene) {
  const floorY = -(TABLE.thickness + TABLE.legHeight);
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(4000, 4000),
    new THREE.MeshStandardMaterial({ color: 0x0b0e13, roughness: 0.92, metalness: 0.0 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = floorY;
  floor.receiveShadow = true;
  scene.add(floor);

  const grid = new THREE.GridHelper(2400, 96, 0x1b2433, 0x121823);
  grid.position.y = floorY + 0.05;
  scene.add(grid);

  // Back wall panel with a faint lab-strip glow.
  const wall = new THREE.Mesh(
    new THREE.PlaneGeometry(900, 320),
    new THREE.MeshStandardMaterial({ color: 0x0a0d13, roughness: 0.95 }),
  );
  wall.position.set(0, floorY + 160, -230);
  scene.add(wall);
  const strip = new THREE.Mesh(new THREE.PlaneGeometry(700, 1.2), new THREE.MeshBasicMaterial({ color: 0x1fb6d1 }));
  strip.position.set(0, floorY + 230, -229.5);
  scene.add(strip);
  return wall;
}

function buildTable(boardTex) {
  const { width: W, depth: D, thickness: T, legHeight: L } = TABLE;
  const g = new THREE.Group();
  g.name = 'table';

  const tex = boardTex.clone();
  tex.repeat.set(W / GRID, D / GRID);
  tex.needsUpdate = true;
  const top = new THREE.Mesh(
    new THREE.PlaneGeometry(W, D),
    new THREE.MeshStandardMaterial({ map: tex, metalness: 0.55, roughness: 0.5 }),
  );
  top.rotation.x = -Math.PI / 2;
  top.receiveShadow = true;
  top.name = 'tabletop';
  g.add(top);

  const steel = new THREE.MeshStandardMaterial({ color: 0x3d434f, metalness: 0.8, roughness: 0.35 });
  const skin = new THREE.Mesh(new THREE.BoxGeometry(W + 0.6, 1.2, D + 0.6), steel);
  skin.position.y = -0.61;
  skin.receiveShadow = true;

  const body = new THREE.Mesh(
    new THREE.BoxGeometry(W, T - 1.2, D),
    new THREE.MeshStandardMaterial({ color: 0x15181f, metalness: 0.35, roughness: 0.65 }),
  );
  body.position.y = -1.2 - (T - 1.2) / 2;
  body.castShadow = true;

  // Thin accent line round the table edge.
  const trim = new THREE.Mesh(new THREE.BoxGeometry(W + 0.7, 0.25, D + 0.7), new THREE.MeshBasicMaterial({ color: 0x0e7490 }));
  trim.position.y = -3.2;
  g.add(skin, body, trim);

  // Pneumatic isolator legs: more of them under long tables.
  const legMat = new THREE.MeshStandardMaterial({ color: 0x1d222b, metalness: 0.6, roughness: 0.4 });
  const ringMat = new THREE.MeshStandardMaterial({ color: 0x0f1218, metalness: 0.4, roughness: 0.6 });
  const legGeo = new THREE.CylinderGeometry(7, 8, L, 32);
  const ringGeo = new THREE.CylinderGeometry(8.6, 8.6, 3, 32);
  const perSide = W > 330 ? 4 : W > 200 ? 3 : 2;
  const inset = Math.min(13, W / 6);
  for (let i = 0; i < perSide; i++) {
    const x = -(W / 2 - inset) + (i * (W - 2 * inset)) / (perSide - 1);
    for (const sz of [-1, 1]) {
      const z = sz * (D / 2 - Math.min(12, D / 5));
      const leg = new THREE.Mesh(legGeo, legMat);
      leg.position.set(x, -T - L / 2, z);
      leg.castShadow = leg.receiveShadow = true;
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.position.set(x, -T - 9, z);
      g.add(leg, ring);
    }
  }
  for (const sz of [-1, 1]) {
    const beam = new THREE.Mesh(new THREE.BoxGeometry(W - 2 * inset, 4, 4), legMat);
    beam.position.set(0, -T - L + 14, sz * (D / 2 - Math.min(12, D / 5)));
    g.add(beam);
  }
  return g;
}

/** Two-board shelf behind a table (built at x = 0): the setup's oscilloscopes and counters sit
 * on the upper board, the laptops and the network switch on the lower one. The posts stand at
 * the boards' back edge, out of the way of what sits on them. */
function buildShelf() {
  const { width: W } = TABLE;
  const z0 = SHELF.z;
  const g = new THREE.Group();
  g.name = 'shelf';
  const postMat = new THREE.MeshStandardMaterial({ color: 0x2a303b, metalness: 0.85, roughness: 0.3 });
  const posts = W > 330 ? 4 : W > 200 ? 3 : 2;
  for (let i = 0; i < posts; i++) {
    const post = new THREE.Mesh(new THREE.BoxGeometry(3, 82, 3), postMat);
    post.position.set(-(W / 2 - 2) + (i * (W - 4)) / (posts - 1), 21, z0 - SHELF.depth / 2 + 1.5);
    post.castShadow = true;
    g.add(post);
  }
  const boardMat = new THREE.MeshStandardMaterial({ color: 0x1a1f28, metalness: 0.5, roughness: 0.5 });
  for (const top of [SHELF.y, SHELF.lowerY]) {
    const board = new THREE.Mesh(new THREE.BoxGeometry(W, 1.6, SHELF.depth), boardMat);
    board.position.set(0, top - 0.8, z0);
    board.castShadow = board.receiveShadow = true;
    g.add(board);
  }
  return g;
}
