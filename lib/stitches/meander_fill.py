import networkx as nx
from shapely.geometry import MultiPoint, Point
from shapely.ops import nearest_points

from .running_stitch import running_stitch
from .. import tiles
from ..debug import debug
from ..stitch_plan import Stitch
from ..utils.smoothing import smooth_path
from ..utils.geometry import Point as InkStitchPoint, ensure_geometry_collection
from ..utils.list import poprandom
from ..utils.prng import iter_uniform_floats
from ..utils.threading import check_stop_flag


def meander_fill(fill, shape, shape_index, starting_point, ending_point):
    debug.log(f"meander pattern: {fill.meander_pattern}")
    tile = get_tile(fill.meander_pattern)
    if not tile:
        return []

    debug.log(f"tile name: {tile.name}")

    debug.log_line_strings(lambda: ensure_geometry_collection(shape.boundary).geoms, 'Meander shape')
    graph = tile.to_graph(shape, fill.meander_scale)
    debug.log_graph(graph, 'Meander graph')
    debug.log(lambda: f"graph connected? {nx.is_connected(graph)}")
    start, end = find_starting_and_ending_nodes(graph, shape, starting_point, ending_point)
    rng = iter_uniform_floats(fill.random_seed, 'meander-fill', shape_index)

    return post_process(generate_meander_path(graph, start, end, rng), fill)


def get_tile(tile_name):
    all_tiles = {tile.name: tile for tile in tiles.all_tiles()}

    try:
        return all_tiles.get(tile_name, all_tiles.popitem()[1])
    except KeyError:
        return None


def find_starting_and_ending_nodes(graph, shape, starting_point, ending_point):
    if starting_point is None:
        starting_point = shape.exterior.coords[0]
    starting_point = Point(starting_point)

    if ending_point is None:
        ending_point = starting_point
    else:
        ending_point = Point(ending_point)

    all_points = MultiPoint(list(graph))

    starting_node = nearest_points(starting_point, all_points)[1].coords[0]
    ending_node = nearest_points(ending_point, all_points)[1].coords[0]

    if starting_node == ending_node:
        # We need a path to start with, so pick a new ending node
        all_points = all_points.difference(Point(starting_node))
        ending_node = nearest_points(ending_point, all_points)[1].coords[0]

    return starting_node, ending_node


def find_initial_path(graph, start, end):
    # We need some path to start with.  We could use
    # nx.all_simple_paths(graph, start, end) and choose the first one.
    # However, that tends to pick a really "orderly" path.  Shortest
    # path looks more random.

    # TODO: handle if this can't find a path
    return nx.shortest_path(graph, start, end)


@debug.time
def generate_meander_path(graph, start, end, rng):
    path = find_initial_path(graph, start, end)
    path_edges = list(zip(path[:-1], path[1:]))
    graph.remove_edges_from(path_edges)
    graph_nodes = set(graph) - set(path)

    edges_to_consider = list(path_edges)
    meander_path = path_edges
    while edges_to_consider:
        while edges_to_consider:
            check_stop_flag()

            edge = poprandom(edges_to_consider, rng)
            edges_to_consider.extend(replace_edge(meander_path, edge, graph, graph_nodes))

        edge_pairs = list(zip(meander_path[:-1], meander_path[1:]))
        while edge_pairs:
            check_stop_flag()

            edge1, edge2 = poprandom(edge_pairs, rng)
            edges_to_consider.extend(replace_edge_pair(meander_path, edge1, edge2, graph, graph_nodes))
            break

    return path_to_points(meander_path)


def replace_edge(path, edge, graph, graph_nodes):
    subgraph = graph.subgraph(graph_nodes | set(edge))
    new_path = None
    for new_path in nx.all_simple_edge_paths(subgraph, edge[0], edge[1], 7):
        if len(new_path) > 1:
            break
    if new_path is None or len(new_path) == 1:
        return []
    i = path.index(edge)
    path[i:i + 1] = new_path
    graph.remove_edges_from(new_path)
    # do I need to remove the last one too?
    graph_nodes.difference_update(start for start, end in new_path)
    # debug.log(f"found new path of length {len(new_path)} at position {i}")

    return new_path


def replace_edge_pair(path, edge1, edge2, graph, graph_nodes):
    subgraph = graph.subgraph(graph_nodes | {edge1[0], edge2[1]})
    new_path = None
    for new_path in nx.all_simple_edge_paths(subgraph, edge1[0], edge2[1], 10):
        if len(new_path) > 2:
            break
    if new_path is None or len(new_path) <= 2:
        return []
    i = path.index(edge1)
    path[i:i + 2] = new_path
    graph.remove_edges_from(new_path)
    # do I need to remove the last one too?
    graph_nodes.difference_update(start for start, end in new_path)
    # debug.log(f"found new pair path of length {len(new_path)} at position {i}")

    return new_path


@debug.time
def post_process(points, fill):
    debug.log(f"smoothness: {fill.smoothness}")
    # debug.log_line_string(LineString(points), "pre-smoothed", "#FF0000")
    smoothed_points = smooth_path(points, fill.smoothness)
    smoothed_points = [InkStitchPoint.from_tuple(point) for point in smoothed_points]

    stitches = running_stitch(smoothed_points, fill.running_stitch_length, fill.running_stitch_tolerance)
    stitches = [Stitch(point) for point in stitches]

    return stitches


def path_to_points(path):
    points = [start for start, end in path]
    if path:
        points.append(path[-1][1])

    return points
