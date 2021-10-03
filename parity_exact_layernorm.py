import torch
import math
import random
import encoder

log_sigmoid = torch.nn.LogSigmoid()

perturb = 0.01
train = True
n_max = 100
num_epochs = 100
num_steps = 100

class FirstLayer(torch.nn.TransformerEncoderLayer):
    def __init__(self):
        super().__init__(20, 2, 3, dropout=0.)
        self.self_attn.in_proj_weight = torch.nn.Parameter(torch.tensor(
            # First head attends to all symbols,
            # second head does nothing.
            # W^Q
            [[0]*20]*20 +
            # W^K
            [[0]*20]*20 +
            # W^V
            [[0,1,0,0,0,0,0,0,0,0]+[0]*10,   # count 1s  (k)
             [0,0,1,0,0,0,0,0,0,0]+[0]*10]+   # count CLS (1)
            [[0]*20]*18,
            dtype=torch.float))

        self.self_attn.in_proj_bias = torch.nn.Parameter(torch.zeros(60))

        self.self_attn.out_proj.weight = torch.nn.Parameter(torch.tensor(
            # W^O
            [[0]*20]*5 +
            [[1,0,0,0,0,0,0,0,0,0]+[0]*10,   # put new values into dims 5-6
             [0,1,0,0,0,0,0,0,0,0]+[0]*10] +
            [[0]*20]*3 +
            [[0]*20]*5 +
            [[-1,0,0,0,0,0,0,0,0,0]+[0]*10,   # put new values into dims 5-6
             [0,-1,0,0,0,0,0,0,0,0]+[0]*10] +
            [[0]*20]*3,
            dtype=torch.float))
        self.self_attn.out_proj.bias = torch.nn.Parameter(torch.zeros(20))

        self.linear1.weight = torch.nn.Parameter(torch.tensor([
            [0,0,0,-1,0,1,-1,0,0,0]+[0]*10,  # k-i-1
            [0,0,0,-1,0,1, 0,0,0,0]+[0]*10,  # k-i
            [0,0,0,-1,0,1, 1,0,0,0]+[0]*10,  # k-i+1
        ], dtype=torch.float))
        self.linear1.bias = torch.nn.Parameter(torch.zeros(3))
        self.linear2.weight = torch.nn.Parameter(torch.tensor(
            [[0, 0, 0]]*7 +
            [[1,-2, 1]] +  # put I[i=c1] in dim 7
            [[0, 0, 0]]*9 +
            [[-1,2, -1]] +  # put I[i=c1] in dim 7
            [[0, 0, 0]]*2,
            dtype=torch.float))
        self.linear2.bias = torch.nn.Parameter(torch.zeros(20))
    
    def forward(self, src, src_mask=None, src_key_padding_mask=None):
        src2 = self.self_attn(src, src, src, attn_mask=src_mask,
                              key_padding_mask=src_key_padding_mask)[0]
        src = src + self.dropout1(src2)
        src = self.norm1(src)
        src2 = self.linear2(self.dropout(self.activation(self.linear1(src))))
        src = src + self.dropout2(src2)
        src = self.norm2(src)
        return src

class SecondLayer(torch.nn.TransformerEncoderLayer):
    def __init__(self):
        super().__init__(20, 2, 3, dropout=0.)
        self.self_attn.in_proj_weight = torch.nn.Parameter(torch.tensor(
            # W^Q
            # Heads 1 and 2 attend from CLS
            [[0,0,1,0,0,0,0,0,0,0]+[0]*10] +
            [[0]*20]*9 +
            [[0,0,1,0,0,0,0,0,0,0]+[0]*10] +
            [[0]*20]*9 +
            # W^K
            # Head 1 attends to odd positions
            [[0,0,0,0, 1,0,0,0,0,0]+[0]*10] +
            [[0]*20]*9 +
            # Head 2 attends to even positions
            [[0,0,0,0,-1,0,0,0,0,0]+[0]*10] +
            [[0]*20]*9 +
            # W^V
            # Heads 1 and 2 average dim 7
            [[0,0,0,0,0,0,0,1,0,0]+[0]*10] +
            [[0]*20]*9 +
            [[0,0,0,0,0,0,0,1,0,0]+[0]*10] +
            [[0]*20]*9,
            dtype=torch.float))

        self.self_attn.in_proj_bias = torch.nn.Parameter(torch.zeros(60))

        self.self_attn.out_proj.weight = torch.nn.Parameter(torch.tensor(
            # W^O
            # Even positions minus odd positions
            # Place in dim 8
            [[0]*20]*8 +
            [[-1]+[0]*9+[1]+[0]*9,
             [0]*20]+
            [[0]*20]*8 +
            [[1]+[0]*9+[-1]+[0]*9,
             [0]*20],
            dtype=torch.float))
        self.self_attn.out_proj.bias = torch.nn.Parameter(torch.zeros(20))

        # Because of ReLU, first half gets the positive values, second half gets |negative values|
        self.linear1.weight = torch.nn.Parameter(torch.eye(20, dtype=torch.float))
        self.linear1.bias = torch.nn.Parameter(torch.zeros(20))
        
        w = torch.cat((torch.cat((-torch.eye(10), torch.eye(10)), dim=1),
                       torch.cat((torch.eye(10), -torch.eye(10)), dim=1)))
        # Preserve dim 8
        w[8,8] = w[8,18] = w[18,8] = w[18,18] = 0
        self.linear2.weight = torch.nn.Parameter(w.to(torch.float))
        self.linear2.bias = torch.nn.Parameter(torch.zeros(20))

    def forward(self, src, src_mask=None, src_key_padding_mask=None):
        q = src
        v = src
        src2 = self.self_attn(q, src, v, attn_mask=src_mask,
                              key_padding_mask=src_key_padding_mask)[0]
        src = src + self.dropout1(src2)
        src = self.norm1(src)
        src2 = self.linear2(self.dropout(self.activation(self.linear1(src))))
        src = src + self.dropout2(src2)
        src = self.norm2(src)
        return src

class MyTransformerEncoder(torch.nn.TransformerEncoder):
    def __init__(self):
        torch.nn.Module.__init__(self)

        self.layers = torch.nn.ModuleList([
            FirstLayer(),
            SecondLayer(),
        ])
        self.num_layers = len(self.layers)
        self.norm = None

class Model(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.word_embedding = torch.eye(3, 10)
        self.transformer_encoder = MyTransformerEncoder()
        self.output_layer = torch.nn.Linear(20, 1)
        self.output_layer.weight = torch.nn.Parameter(torch.tensor(
            [[0,0,0,0,0,0,0,0,1,0]+[0]*10],
            dtype=torch.float))
        self.output_layer.bias = torch.nn.Parameter(torch.tensor([0.]))

    def forward(self, w):
        p = torch.stack([torch.zeros(len(w))]*3 +
                        [torch.arange(0, len(w), dtype=torch.float) / len(w),
                         torch.cos(torch.arange(0, len(w), dtype=torch.float)*math.pi)] +
                        [torch.zeros(len(w))]*5,
                        dim=1)
        x = torch.cat([self.word_embedding[w] + p,
                       -(self.word_embedding[w] + p)],
                      dim=1)
        y = self.transformer_encoder(x.unsqueeze(1)).squeeze(1)
        z = self.output_layer(y[-1])
        return z

model = Model()
optim = torch.optim.Adam(model.parameters(), lr=3e-4)

best_epoch_loss = float('inf')
no_improvement = 0

# Perturb parameters
if perturb > 0:
    with torch.no_grad():
        for p in model.parameters():
            p += torch.randn(p.size()) * perturb

for epoch in range(num_epochs):
    epoch_loss = 0
    epoch_steps = 0
    epoch_correct = 0
    
    for step in range(num_steps):
        n = random.randrange(1, n_max+1)
        w = torch.tensor([random.randrange(2) for i in range(n)]+[2])
        label = len([a for a in w if a == 1]) % 2 == 1
        output = model(w)

        # Cross-entropy loss
        if not label: output = -output
        if output > 0: epoch_correct += 1
        loss = -log_sigmoid(output)
        epoch_loss += loss.item()
        epoch_steps += 1
        optim.zero_grad()
        loss.backward()
        if train:
            optim.step()

    if epoch_loss < best_epoch_loss:
        best_epoch_loss = epoch_loss
        no_improvement = 0
    else:
        no_improvement += 1
        if no_improvement >= 10:
            optim.param_groups[0]['lr'] *= 0.5
            print(f"lr={optim.param_groups[0]['lr']}")
            no_improvement = 0
        
    test_loss = 0
    test_steps = 0
    test_correct = 0
    for step in range(num_steps):
        n = random.randrange(1, n_max+1)
        w = torch.tensor([random.randrange(2) for i in range(n)]+[2])
        label = len([a for a in w if a == 1]) % 2 == 1
        output = model(w)
        
        # Cross-entropy loss
        if not label: output = -output
        if output > 0: test_correct += 1
        loss = -log_sigmoid(output)
        test_loss += loss.item()
        test_steps += 1

    print(f'n_max={n_max} train_ce={epoch_loss/epoch_steps/math.log(2)} train_acc={epoch_correct/epoch_steps} test_ce={test_loss/test_steps/math.log(2)} test_acc={test_correct/test_steps}')
