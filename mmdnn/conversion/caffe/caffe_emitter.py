#----------------------------------------------------------------------------------------------
#  Copyright (c) Microsoft Corporation. All rights reserved.
#  Licensed under the MIT License. See License.txt in the project root for license information.
#----------------------------------------------------------------------------------------------

import os
import sys
import numpy as np

import caffe
from caffe import layers as L
from caffe import params as P
from mmdnn.conversion.common.IR.IR_graph import IRGraph, IRGraphNode
import mmdnn.conversion.common.IR.graph_pb2 as graph_pb2
from mmdnn.conversion.common.IR.graph_pb2 import NodeDef, GraphDef, DataType
from mmdnn.conversion.common.DataStructure.emitter import Emitter
from mmdnn.conversion.common.utils import *

class CaffeEmitter(Emitter):

    def __init__(self, model):
        from six import string_types as _string_types
        super(CaffeEmitter, self).__init__()
        if isinstance(model, _string_types):
            network_path = model
        else:
            network_path = model[0]
            self._load_weights(model[1])

        self.IR_graph = IRGraph(network_path)
        super(CaffeEmitter, self)._build()


    @property
    def header_code(self):
        return """from __future__ import print_function
import numpy as np
import sys, argparse
import caffe
from caffe import layers as L
from caffe import params as P
from caffe import to_proto
from six import text_type as _text_type


__weights_dict = dict()

def load_weights(weight_file):
    if weight_file == None:
        return

    try:
        weights_dict = np.load(weight_file).item()
    except:
        weights_dict = np.load(weight_file, encoding='bytes').item()

    return weights_dict


def KitModel(weight_file = None):
    n = caffe.NetSpec()
"""

    @property
    def end_code(self):
        return """    return n

def make_net(prototxt):
    n = KitModel()
    with open(prototxt, 'w') as fpb:
        print(n.to_proto(), file=fpb)

def gen_weight(weight_file, model, prototxt):
    global __weights_dict
    __weights_dict = load_weights(weight_file)

    net = caffe.Net(prototxt, caffe.TRAIN)

    for key in __weights_dict:
        if 'weights' in __weights_dict[key]:
            net.params[key][0].data.flat = __weights_dict[key]['weights']
        elif 'mean' in __weights_dict[key]:
            net.params[key][0].data.flat = __weights_dict[key]['mean']
            net.params[key][1].data.flat = __weights_dict[key]['var']
            if 'scale' in __weights_dict[key]:
                net.params[key][2].data.flat = __weights_dict[key]['scale']
        elif 'scale' in __weights_dict[key]:
            net.params[key][0].data.flat = __weights_dict[key]['scale']
        if 'bias' in __weights_dict[key]:
            net.params[key][1].data.flat = __weights_dict[key]['bias']
        if 'gamma' in __weights_dict[key]: # used for prelu, not sure if other layers use this too
            net.params[key][0].data.flat = __weights_dict[key]['gamma']
    net.save(model)
    return net



if __name__=='__main__':
    parser = argparse.ArgumentParser(description='Generate caffe model and prototxt')
    parser.add_argument('--weight_file', '-w', type=_text_type, default='IR weight file')
    parser.add_argument('--prototxt', '-p', type=_text_type, default='caffe_converted.prototxt')
    parser.add_argument('--model', '-m', type=_text_type, default='caffe_converted.caffemodel')
    args = parser.parse_args()
    # For some reason argparser gives us unicode, so we need to conver to str first
    make_net(str(args.prototxt))
    gen_weight(str(args.weight_file), str(args.model), str(args.prototxt))

"""

    def gen_code(self, phase = 'test'):
        self.phase = phase
        self.add_body(0, self.header_code)

        #for test
        # with open("graph.txt", 'w') as f:
        #     for layer in self.IR_graph.topological_sort:
        #         current_node = self.IR_graph.get_node(layer)
        #         print("========current_node=========\n{}".format(current_node.layer), file=f)
        #test end

        for layer in self.IR_graph.topological_sort:
            current_node = self.IR_graph.get_node(layer)
            node_type = current_node.type
            #print("========current_node={}".format(current_node.layer))

            if hasattr(self, "emit_" + node_type):
                func = getattr(self, "emit_" + node_type)
                func(current_node)
            else:
                print("CaffeEmitter has not supported operator [%s]." % (node_type))
                self.emit_UNKNOWN(current_node)

        self.add_body(0, "")
        self.add_body(0,self.end_code)

        return self.body_code


    def run(self, dstNetworkPath, dstWeightPath = None, phase = 'test'):
        super(CaffeEmitter, self).run(dstNetworkPath, dstWeightPath, phase)
        if self.weight_loaded:
            self.save_weights(self.weights_dict, dstWeightPath)



    @staticmethod
    def _shapeToStr(shapes):
        return [dim.size if dim.size > 0 else 1 for dim in shapes.dim]



    def check_if_need_transpose(self, IR_node):
        parent = self.IR_graph.get_parent(IR_node.name, [0])
        while parent.type == 'Flatten':
            parent = self.IR_graph.get_parent(parent.name, [0])
        dim = len(parent.layer.attr['_output_shapes'].list.shape[0].dim)
        if dim > 2:
            original_dims = self.weights_dict[IR_node.name]['weights'].shape
            dims = [i.size for i in parent.layer.attr['_output_shapes'].list.shape[0].dim[1:]] + [-1]
            self.weights_dict[IR_node.name]['weights'] = np.reshape(self.weights_dict[IR_node.name]['weights'], dims)
            self.weights_dict[IR_node.name]['weights'] = np.transpose(self.weights_dict[IR_node.name]['weights'], [dim - 2] + list(range(0, dim - 2)) + [dim - 1])
            self.weights_dict[IR_node.name]['weights'] = np.reshape(self.weights_dict[IR_node.name]['weights'], original_dims)


    def emit_Conv(self, IR_node):
        #check if have pad layer
        pad_h = 0
        pad_w = 0
        IR_parent_node = self.IR_graph.get_parent(IR_node.name, [0])
        if IR_parent_node.type == 'Pad':
            pad_h = IR_parent_node.get_attr('pads')[1]
            pad_w = IR_parent_node.get_attr('pads')[2]
        else:
            pad_h = IR_node.get_attr('pads')[1]
            pad_w = IR_node.get_attr('pads')[2]

        self.add_body(1, "n.{:<15} = L.Convolution(n.{}, kernel_size={}, stride={}, num_output={}, pad_h={}, pad_w={}, group={}, \
bias_term={}, ntop=1)".format(
            IR_node.variable_name,
            self.parent_variable_name(IR_node),
            IR_node.get_attr('kernel_shape')[0],
            IR_node.get_attr('strides')[1],
            IR_node.get_attr('kernel_shape')[-1],
            pad_h,
            pad_w,
            IR_node.get_attr('group', 1),
            IR_node.get_attr('use_bias', False)))

        dim = len(IR_node.get_attr('strides')) - 2
        if self.weight_loaded:
            self.weights_dict[IR_node.name]['weights'] = np.transpose(self.weights_dict[IR_node.name]['weights'], [dim + 1, dim] + list(range(0, dim)))
            self.weights_dict[IR_node.variable_name] = self.weights_dict.pop(IR_node.name)

        # keys = []
        # for key in self.weights_dict[IR_node.name].keys():
        #     keys.append(key)
        # print("=======Layer: {}, keys: {}".format(IR_node.name, keys))

    def emit_Pool(self, IR_node):
        pooling_type = IR_node.get_attr('pooling_type')
        if pooling_type == 'MAX':
            pooling_type = P.Pooling.MAX
        elif pooling_type == 'AVG':
            pooling_type = P.Pooling.AVE
        elif pooling_type == 'STOCHASTIC':
            pooling_type = P.Pooling.STOCHASTIC
        else:
            raise ValueError

        if IR_node.layer.attr['global_pooling'].b:
            self.used_layers.add('GlobalPooling')
            self.add_body(1, "n.{:<15} = L.Pooling(n.{}, pool={}, stride={}, global_pooling=True, ntop=1)".format(
                IR_node.variable_name,
                self.parent_variable_name(IR_node),
                pooling_type,
                IR_node.get_attr('strides')[1]))
        else:
            self.add_body(1, "n.{:<15} = L.Pooling(n.{}, pool={}, kernel_size={}, pad_h={}, pad_w={}, stride={}, ntop=1)".format(
                IR_node.variable_name,
                self.parent_variable_name(IR_node),
                pooling_type,
                IR_node.get_attr('kernel_shape')[1],
                IR_node.get_attr('pads')[1],
                IR_node.get_attr('pads')[2],
                IR_node.get_attr('strides')[1]))


    def emit_UNKNOWN(self, IR_node):
        print(IR_node.IR_layer.name)


    def emit_DataInput(self, IR_node):
        shape = self._shapeToStr(IR_node.get_attr('shape'))
        shape = [shape[0], shape[-1]] + shape[1:-1]
        self.add_body(1, "n.{:<15} = L.Input(shape=[dict(dim={})], ntop=1)".format(
            IR_node.variable_name,
            shape))


    def emit_Dropout(self, IR_node):
        in_place = True
        self.add_body(1, "n.{:<15} = L.Dropout(n.{}, dropout_ratio={} , in_place={}, ntop=1)".format(
            IR_node.variable_name,
            self.parent_variable_name(IR_node),
            1 - IR_node.get_attr('keep_prob'),
            in_place))


    def emit_FullyConnected(self, IR_node):
        self.add_body(1, "n.{:<15} = L.InnerProduct(n.{}, num_output={}, bias_term={}, ntop=1)".format(
            IR_node.variable_name,
            self.parent_variable_name(IR_node),
            IR_node.layer.attr["units"].i,
            IR_node.get_attr('use_bias', False)))
        if self.weight_loaded:
            self.check_if_need_transpose(IR_node)
            self.weights_dict[IR_node.name]['weights'] = np.transpose(self.weights_dict[IR_node.name]['weights'], (1, 0))
            self.weights_dict[IR_node.variable_name] = self.weights_dict.pop(IR_node.name)


    def emit_BatchNorm(self, IR_node):
        self.add_body(1, "n.{:<15} = L.BatchNorm(n.{}, eps={}, use_global_stats={}, ntop=1)".format(
            IR_node.variable_name,
            self.parent_variable_name(IR_node),
            IR_node.get_attr('epsilon'),
            self.phase == 'test'
        ))

        scale_layer_var_name = IR_node.variable_name + "_scale"
        # Since the scale layer is "almost part" of the bn layer, we can safely use in_place here.
        self.add_body(1, "n.{:<15} = L.Scale(n.{}, bias_term={}, in_place=True, ntop=1)".format(
            scale_layer_var_name,
            IR_node.variable_name,
            IR_node.get_attr('bias', False)
        ))

        if self.weight_loaded:
            self.weights_dict[scale_layer_var_name] = dict()
            if 'scale' in self.weights_dict[IR_node.name]:
                self.weights_dict[scale_layer_var_name]['scale'] = self.weights_dict[IR_node.name]['scale']
                #self.weights_dict[IR_node.name].pop('scale', None)
                self.weights_dict[IR_node.name]['scale'] = 1
            self.weights_dict[scale_layer_var_name]['bias'] = self.weights_dict[IR_node.name]['bias']
            self.weights_dict[IR_node.name].pop('bias', None)
            self.weights_dict[IR_node.variable_name] = self.weights_dict.pop(IR_node.name)

        IR_node.real_name = IR_node.name + "_scale"


    def emit_LRN(self, IR_node):
        self.add_body(1, "n.{:<15} = L.LRN(n.{}, local_size={}, alpha={}, beta={}, k={})".format(
            IR_node.variable_name,
            self.parent_variable_name(IR_node),
            IR_node.get_attr('size') * 2 - 1,
            IR_node.get_attr('alpha'),
            IR_node.get_attr('beta'),
            IR_node.get_attr('k')
        ))


    def emit_Add(self, IR_node):
        input_layers = ', '.join(('n.' + self.IR_graph.get_parent(IR_node.name, [num]).real_variable_name) for num in range(0, len(IR_node.in_edges)))
        self.add_body(1, "n.{:<15} = L.Eltwise({}, operation=1, ntop=1)".format(
            IR_node.variable_name,
            input_layers,
        ))

    def emit_Flatten(self, IR_node):
        IR_node.real_name = self.IR_graph.get_parent(IR_node.name, [0]).real_name


    def emit_Squeeze(self, IR_node):
        IR_node.real_name = self.IR_graph.get_parent(IR_node.name, [0]).real_name


    def emit_Concat(self, IR_node):
        axis_array = (2, 3, 1, 0)
        axis = axis_array.index(IR_node.get_attr('axis'))
        input_layers = ', '.join(('n.' + self.IR_graph.get_node(edge).real_variable_name) for edge in IR_node.in_edges)
        self.add_body(1, "n.{:<15} = L.Concat({}, axis={})".format(
            IR_node.variable_name,
            input_layers,
            axis
        ))

    # def emit_Tanh(self, IR_node):
    #     self._emit_activation(IR_node, 'ops.tanh')


    def emit_Relu(self, IR_node):
        in_place = True
        self.add_body(1, "n.{:<15} = L.ReLU(n.{}, in_place={}, ntop=1)".format(
            IR_node.variable_name,
            self.parent_variable_name(IR_node),
            in_place))


    def emit_PRelu(self, IR_node):
        in_place = True
        self.add_body(1, "n.{:<15} = L.PReLU(n.{}, in_place={}, ntop=1)".format(
            IR_node.variable_name,
            self.parent_variable_name(IR_node),
            in_place))


    def emit_Softmax(self, IR_node):
        self.add_body(1, "n.{:<15} = L.Softmax(n.{}, ntop=1)".format(
            IR_node.variable_name,
            self.parent_variable_name(IR_node)))


    def emit_Pad(self, IR_node):
        IR_node.real_name = self.IR_graph.get_parent(IR_node.name, [0]).real_name

    def reduction(self, IR_node, op, axes):
        # Convert NHWC (IR) to NCHW (Caffe): [0,1,2,3]->[0,3,1,2]
        if len(axes) == 1:
            assert (axes[0] == 2)
        elif len(axes) == 2:
            assert ((axes[0] == 1) and (axes[1] == 2))

        self.add_body(1, "n.{:<15} = L.Reduction(n.{}, operation={} , axis={} ,ntop=1)".format(
            IR_node.variable_name,
            self.parent_variable_name(IR_node),
            op,
            len(axes)))

    def emit_ReduceMean(self, IR_node):
        self.reduction(IR_node, 4 , IR_node.get_attr('axes'))

    def emit_ReduceSum(self, IR_node):
        self.reduction(IR_node, 1, IR_node.get_attr('axes'))
