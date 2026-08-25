# Decisões, trade-offs e limitações

Aqui está o que eu escolhi, contra o que, e por quê. O que o código já mostra sozinho eu
deixei de fora.

Aviso: boa parte disso eu só descobri depois de rodar e ver o que dava errado. Não foi
tudo planejado antes. Onde mudei de ideia no meio, deixei registrado o que eu tinha feito
primeiro e o que me fez mudar — acho que isso conta mais do que fingir que saiu certo de
primeira.

---

## 1. Tratamento de dados

### 1.1 Duplicatas

Os dois arquivos têm linhas repetidas: 1 no Nível 1 (`OP-0007`) e 5 no Nível 2, todas com
todos os campos iguais, inclusive o ID.

Antes de remover, chequei se algum ID repetido tinha conteúdo diferente. Nenhum tinha, e
isso importa: se o conteúdo é idêntico, é reenvio do sistema legado e dá pra apagar
tranquilo. Se fosse diferente, eu não teria como decidir sozinho qual linha vale, e isso
teria que virar exceção pra alguém olhar na mão. Deixei essa checagem visível no
`diagnosticar()` em vez de só dar um `drop_duplicates()` e seguir em frente.

**Por que isso não é só arrumação:** sem deduplicar, o `CLI-A-3` fica com 4 operações no
mesmo dia somando R$ 65.700 e **dispara a Regra 1 de fracionamento**. Tirando a linha
repetida: 3 operações, R$ 48.500, não dispara. Ou seja, o erro do sistema criava um alerta
de PLD que não existia. No Nível 2, a duplicata muda a mediana e altera o status do
`CLI-028` na Regra 2.

### 1.2 Operações sem data

São 7 no Nível 2 (6 depois de tirar as duplicatas) e 1 no Nível 1. Todas com `data: null`
e a observação do próprio sistema: "data nao capturada pelo sistema". O valor está lá e faz
sentido, então é falha de captura de um campo, não registro quebrado.

Pensei em três saídas:

| Opção | Por que não usei |
|---|---|
| Apagar a linha | Tira dinheiro real do volume do cliente e uma operação da conta da Regra 2. Em PLD, jogar fora operação por causa de falha do sistema faz o risco parecer menor do que é. Testei: apagar muda o status do `CLI-029` na Regra 2 |
| Chutar uma data | Isso é inventar prova. Se a data que eu chutasse caísse junto com outras operações do cliente, eu **criaria** um alerta de fracionamento do nada |
| **Manter e marcar `sem_data=True`** ✅ | A Regra 1 depende de data e ignora essas linhas; a Regra 2 e o volume continuam usando |

Fui na terceira. Num sistema de verdade isso também deveria gerar um aviso pro time que
manda os dados, porque o erro está lá na origem.

### 1.3 Moeda estrangeira

7 operações em USD no Nível 2 e 1 no Nível 1, todas com observação "remessa internacional"
e canal TED. Converti pela taxa que vem dentro do próprio arquivo (5,4) e criei a coluna
`valor_brl`. Daí pra frente tudo usa `valor_brl`, nunca `valor`.

Sem converter, a Regra 2 **não sinaliza ninguém** no Nível 1 e perde 4 clientes no Nível 2
(`CLI-007`, `CLI-022`, `CLI-024`, `CLI-030`). E o pior é que não daria erro nenhum — a
regra rodaria normal e só devolveria menos coisa.

**A ordem `deduplicar → converter → datar` não é à toa.** Se eu calculasse a mediana antes
de deduplicar, a linha repetida entraria duas vezes na conta. Se comparasse com o limite
antes de converter, estaria comparando moedas diferentes. Fiz uma tabela na seção 5 do
notebook do Nível 1 mostrando quanto cada uma dessas coisas muda o resultado.

A conclusão que eu tiro disso: limpar os dados não é preparação pra análise, é parte do
controle. Uma regra certa em cima de dado sujo erra pros dois lados — inventa alerta onde
não tem risco e esconde risco onde tem. E nenhum dos dois casos dá erro na tela.

### 1.4 Contrapartes com nomes parecidos

O Nível 2 tem 111 contrapartes diferentes, várias com nome parecido: `Cristal Comercio
LTDA` e `Cristal Comercio SA`, `Alfa Transportes LTDA` e `Alfa Transportes ME`.

**Decidi não juntar.** LTDA, ME e SA são tipos societários diferentes — podem ser empresas
do mesmo grupo ou podem não ter nada a ver uma com a outra. Sem CNPJ eu não tenho como
saber, e juntar por parecença de nome criaria uma ligação entre empresas que talvez não
exista. Num alerta de PLD, afirmar que duas contrapartes são a mesma sem ter documento é
pior do que não afirmar nada.

Se tivesse mais tempo, resolveria por CNPJ e usaria a semelhança de nome só como pista pra
verificar, nunca como resposta.

### 1.5 O que eu olhei e estava certo

`canal`, `tipo` e `moeda` não têm variação de escrita nem de maiúscula/minúscula. Todos os
valores são positivos, nenhum zero. As datas preenchidas estão todas em ISO. Nenhum
`cliente_id` estranho.

Anoto isso porque não tratar essas coisas foi decisão, não esquecimento. Normalizar campo
que já está normalizado só adiciona código sem adicionar controle nenhum.

---

## 2. As regras

### 2.1 Onde eu coloquei a fronteira

O enunciado está escrito em português normal e cada palavra tem uma leitura possível.
Fiquei com a mais literal:

| No enunciado | Como implementei | Por quê |
|---|---|---|
| "soma **ultrapassa** R\$ 50.000" | `soma > 50_000` | ultrapassar é passar; 50.000 exato não passou |
| "nenhuma operação **atinge** R\$ 20.000" | `max < 20_000` | atingir é chegar; 20.000 exato chegou, então desqualifica |
| "**superior a** 5× a mediana" | `> 5 * mediana` | estrito |
| "clientes com 4 ou mais operações" | `count >= 4`, **depois** da limpeza | senão uma duplicata podia empurrar um cliente de 3 pra 4 operações e fazer ele entrar na regra sem motivo |

Sobre a mediana da Regra 2: deixei a própria operação testada dentro do cálculo. É a
leitura mais direta do texto, e também é a mais conservadora — o valor alto puxa a mediana
pra cima, então a regra dispara menos vezes, não mais.

### 2.2 Volume: somei tudo, sem sinal

O campo `tipo` separa enviada, recebida, pagamento, depósito e saque. Dava pra entender
"volume transacionado" como giro (soma tudo) ou como saldo (entradas menos saídas). Fiquei
com o giro, porque em PLD o que interessa é quanto passou pela conta. Se entra R\$ 100 mil
e sai R\$ 100 mil, no saldo isso zera — e é justamente o padrão que a gente quer enxergar.

### 2.3 Limiares no topo do arquivo

Coloquei `MIN_OPS_FRACIONAMENTO`, `LIMITE_SOMA_FRACIONAMENTO` e os outros como constantes
nomeadas no topo do `pipeline.py`, em vez de espalhados no meio do código. Limiar de
compliance é decisão de política, não de programação — quem define é a área de compliance,
e num sistema real isso viria de configuração pra poder mudar sem mexer no código.

---

## 3. Como organizei o código

### 3.1 O `pipeline.py` mora em `nivel_1/`

Deixei ali porque é no Nível 1 que o enunciado pede o tratamento. O Nível 2 importa o mesmo
arquivo: **a Parte A do Nível 2 não precisou de nenhuma linha nova**, só troquei o caminho
do arquivo de dados. Escrevi como módulo desde o começo justamente esperando essa troca.

O custo disso é ter que fazer `sys.path.insert` nos arquivos de `nivel_2/` que importam de
fora da própria pasta. Ficaria mais limpo com um pacote na raiz do projeto, mas isso sairia
da estrutura de pastas que o enunciado pede pra seguir exatamente. Preferi respeitar a
estrutura e pagar o preço no import.

### 3.2 Cache das respostas em disco

O `llm.py` salva cada resposta num arquivo, indexado por hash do modelo + prompt. Comecei
fazendo isso só pra economizar quota, mas acabou virando essencial — com o limite de tokens
por dia da camada gratuita, sem cache não dá pra ficar testando. Falo mais disso na 5.4.

### 3.3 Erro num cliente não pode derrubar o resto

Nenhuma função do `llm.py` nem do `agente.py` levanta exceção pra fora. Quando dá errado,
vira um status registrado (`erro_api`, `falha_validacao`, `sem_conclusao`). Num lote de dez
clientes, um problema num deles não pode acabar com os outros nove. Isso se provou útil: na
execução final tive 4 falhas e mesmo assim os 6 pareceres bons ficaram salvos.

---

## 4. A saída estruturada

### 4.1 Nível de risco fechado, sem negociação

Usei `nivel_risco: Literal["baixo","medio","alto"]`. Se o modelo responder "altíssimo", a
validação **rejeita**. Isso é o que faz a saída ser aproveitável depois: sem o vocabulário
fechado, o confronto do Nível 2 viraria comparação solta de texto, e um nível inventado
passaria batido sem dar erro nenhum.

### 4.2 `red_flags`: aceitar formato errado, recusar significado errado

Na primeira execução de verdade, o `gpt-oss-120b` devolveu `red_flags` como um texto
separado por ponto e vírgula em vez de lista. E o retry **não resolveu** — as duas
tentativas falharam com o mesmo erro. Isso me mostrou que retry serve pra corrigir
descuido, não pra corrigir um jeito que o modelo simplesmente tem de responder.

Resolvi com um `field_validator` que aceita a string e transforma em lista. Mas fiz a
coerção bem estreita de propósito, e o critério foi esse:

`red_flags` vindo como `"a; b; c"` é **problema de formato** — o conteúdo está inteiro e a
separação é óbvia. Converter isso é o que qualquer camada de integração faz.

Já `nivel_risco` vindo como "altíssimo" seria **problema de significado** — aceitar seria
criar um quarto nível que o resto do sistema não sabe comparar.

Então: converte quando dá pra entender a intenção sem dúvida, recusa quando aceitar
estragaria a análise depois.

### 4.3 O erro que eu mesmo criei no schema

Essa foi a coisa que mais me ensinou no desafio, e confesso que só percebi porque quebrou.

Eu tinha escrito `red_flags` com `min_length=1`, meio no automático — parecia óbvio exigir
pelo menos um indício. Aí rodei o agente no `CLI-029`: ele investigou, concluiu que a
sinalização da regra não se sustentava, devolveu lista vazia, e a **minha validação
rejeitou**.

Ou seja, o schema não deixava o modelo dizer "olhei e não achei nada". Ele só teria duas
opções: inventar um indício pra conseguir responder, ou falhar. Nos dois casos, quem
estaria criando o falso positivo era eu, no schema, não o modelo.

Isso me fez perceber uma coisa que eu não tinha pensado antes: restrição de schema não é
neutra. Ela decide quais conclusões o sistema consegue expressar.

Troquei por uma regra que faz sentido (`_coerencia`): risco **alto** precisa de pelo menos
um indício, porque parecer de risco alto sem evidência não dá pra auditar. Risco baixo pode
não ter nada a apontar mesmo. A exigência passou a depender da conclusão em vez de valer
pra tudo.

---

## 5. LLM e agente

### 5.1 O modelo do enunciado não existe mais

Comecei com o `llama-3.3-70b-versatile`, que o enunciado sugere. Tomei **404** — o modelo
saiu do catálogo do Groq. Listei os modelos que a minha chave alcança e escolhi
`openai/gpt-oss-120b` olhando capacidade, suporte a tool calling e os limites da camada
gratuita. Depois acabei a quota diária dele e migrei pro `openai/gpt-oss-20b` (explico na
5.4), e **rodei o lote inteiro de novo** no modelo novo, porque misturar dois modelos no
mesmo lote estragaria a comparação.

Uma coisa que eu não sabia e mudou o dimensionamento: os `gpt-oss` são modelos de
raciocínio. Numa chamada de teste onde a resposta era literalmente a palavra "ok", 38 dos
48 tokens de saída foram `reasoning_tokens` — pensamento que não aparece na resposta mas
**conta no orçamento**. Com `max_tokens=1200`, o raciocínio comia a cota e o JSON saía
cortado no meio, o que parecia bug de parsing. Subi pra 3000 e ajustei o intervalo entre
chamadas com base nessa medição.

### 5.2 O modelo calcula mesmo quando eu proíbo — vi quatro vezes

Esse foi o achado que mais me surpreendeu, e é o que sustenta a separação entre regra e LLM
na prática.

| Onde | O que ele escreveu | O certo |
|---|---|---|
| Prompt v1 (Nível 1) | "supera em mais de duas vezes o limite" | 64.800 ÷ 27.250 = 2,38× — acertou, mas calculou |
| Prompt v2 (Nível 1) | "supera em mais de cinco vezes a mediana" | 64.800 ÷ 5.450 = **11,9×** — usou o limiar da regra como se fosse a medida |
| Agente, `CLI-014` | citou "limite de 11.542,05" | 2.308,41 × 5 — multiplicou sozinho |
| Agente, `CLI-029` | "os valores estão **acima** do limite típico de reporte" | estavam entre R\$ 14 mil e R\$ 19,4 mil, ou seja **abaixo** do teto de R\$ 20 mil — inverteu |

O caso da v2 é o que mais me chamou atenção, porque o system prompt dizia em caixa alta
`Nao recalcule, nao estime e nao infira nenhum numero que nao esteja no dossie`. E ele
calculou do mesmo jeito.

O padrão que aparece nos quatro é sempre o mesmo: quando envolve **comparar número com
limiar**, ele erra ou distorce, mesmo acertando o resto da análise.

Então a conclusão não é só "a LLM erra conta". Pra mim é mais forte que isso: pedir pra ela
não calcular não funciona como controle. O prompt melhor resolveu outras coisas — corrigiu
a tipologia, cortou red flag inventada, impediu uma conclusão que os dados não sustentavam
— mas não impediu o cálculo. Se o número importa, ele tem que ser calculado fora e entregue
pronto. É por isso que todo número desse projeto vem do pandas.

**E acho que entendi por quê:** o modelo calculou porque precisava daquele dado e ele não
estava no que eu mandei. Proibir não faz a necessidade sumir. A correção certa não é
proibir com mais ênfase, é preencher o buraco no insumo. Volto nisso na seção 8.

### 5.3 Por que isso é agente e não script

O enunciado avisa que chamar todas as ferramentas sempre não conta como agente. Fiz três
coisas pra que a escolha variasse de verdade, sem colocar `if/else` decidindo a rota:

**Só falo qual regra sinalizou o cliente**, e nada mais. Não mando o dossiê inteiro — se
mandasse, o modelo não teria motivo nenhum pra usar ferramenta.

**As docstrings dizem quando cada ferramenta serve e o que ela não responde.** Demorei pra
sacar que em tool calling a docstring não é documentação, é a interface: é literalmente o
que o modelo lê pra decidir. Descrição vaga faz o agente chamar tudo por garantia.

**`emitir_parecer` é uma ferramenta**, o que dá pro modelo um jeito claro de encerrar (5.5).

Como ficou na execução final:

| Cliente | O que sinalizou | Caminho que ele fez |
|---|---|---|
| CLI-029, CLI-017 | fracionamento (num dia específico) | `operacoes_do_dia` → `historico_cliente` |
| CLI-014 | 3 valores atípicos | `historico_cliente` → `operacoes_do_dia` ×3 (um por operação) |
| CLI-001 | valor atípico | `historico_cliente` → `perfil_canal` |
| CLI-005 | valor atípico | só `historico_cliente` |

Nenhum cliente chamou as mesmas ferramentas na mesma ordem. O `CLI-014` foi olhar cada
operação atípica separada, uma por uma — isso eu não programei.

### 5.4 O limite de tokens mudou como eu executo

A camada gratuita do Groq limita por **tokens por dia** (200.000), e o pior é que a janela
repõe devagar: medi mais ou menos 1.400 tokens liberados em vários minutos depois que
estourou. Um lote inteiro gasta uns 50.000. Depois que acabou, fiquei horas sem conseguir
rodar nada.

A saída foi trocar de modelo, porque a cota é **por modelo** — o `gpt-oss-20b` tinha os
200K dele intactos. Registro isso como contorno, não como solução: num sistema de verdade
isso vira planejamento de capacidade, com fila, backoff e orçamento de tokens por execução.

E foi isso que fez o cache deixar de ser otimização e virar requisito.

### 5.5 Recuperando o parecer de dentro do erro

O `gpt-oss-120b` várias vezes tentava entregar o parecer chamando uma ferramenta que não
existe, chamada `"JSON"`. O Groq recusa com erro 400 — **mas devolve o que o modelo tentou
gerar num campo `failed_generation`**, e o parecer completo está lá dentro.

Fiz duas coisas. A estrutural foi criar o `emitir_parecer` como ferramenta de verdade,
assim ele passa a ter um caminho claro pra concluir. A de contingência foi o
`_recuperar_de_erro()`, que pega o parecer de dentro da mensagem de erro. Achei que valia,
porque o modelo já tinha feito a análise inteira e só errou o "envelope" — jogar fora 5.000
tokens de trabalho válido por causa disso seria desperdício. E o texto recuperado passa
pela **mesma validação**, então nada entra sem ser conferido.

**Sendo honesto sobre a limitação:** depender do formato do `failed_generation` é ficar
preso ao Groq. Se eles mudarem a mensagem de erro, quebra. É gambiarra consciente, não
solução definitiva.

Uma coisa boa que não esperava: depois do `emitir_parecer`, o parecer passou a chegar como
**argumento de função com tipo**, com `enum` e `array` que o próprio provedor valida antes
de chegar em mim. Percebi que JSON pedido em texto é sugestão, mas parâmetro de função é
contrato. Mantive o `field_validator` no código como segurança.

### 5.6 Parei de sobrescrever parecer bom

Minha primeira versão do `rodar_lote` gravava por cima sem olhar o que tinha. Uma
reexecução que falhou por quota apagou cinco pareceres que estavam prontos. Corrigi: se o
arquivo em disco tem status `ok` e a execução nova falhou, mantém o antigo. Perder análise
já feita por causa de um erro de infraestrutura não é perder arquivo, é perder trabalho.

---

## 6. O confronto

### 6.1 O critério do enunciado dá conjunto vazio aqui

O enunciado dá como exemplo que "cliente sinalizado pelas duas regras deveria sair como
risco alto". Testei: **nenhum dos 30 clientes dispara as duas.**

E não é coincidência. As duas regras procuram coisas opostas — fracionamento procura
*vários valores médios juntos num dia*, valor atípico procura *um valor destoante do
histórico*. Quem dispara uma tende a não disparar a outra. Elas separam populações
diferentes.

Como o conjunto ficou vazio, tive que inventar outro critério:

| O que a regra apontou | Risco esperado |
|---|---|
| Regra 1 acionada | alto |
| Regra 2 com 2+ operações atípicas | alto |
| Regra 2 com 1 operação atípica | médio |
| Nenhuma | baixo |

Coloquei fracionamento como mais grave de propósito. Fracionamento é **comportamento** —
precisa de várias operações combinadas de propósito. Valor atípico sozinho é
**acontecimento** — pode ser venda de um carro, rescisão, aporte, exportação. Tratar os dois
igual seria confundir as duas coisas.

### 6.2 Deu 0% de concordância exata, e acho que isso diz alguma coisa

Nos 6 clientes que tiveram parecer concluído:

- Concordância exata: **0/6 (0%)**
- Concordância adjacente (erra por um nível): **2/6 (33%)**
- Agente mais brando que a regra: **6**
- Agente mais severo: **0**

O que me chamou atenção não foi o 0%, foi o **6 de 6 na mesma direção**. Se o agente
estivesse errando aleatório, teria caso em que ele é mais duro que a regra. Não tem nenhum.
Errar sempre pro mesmo lado parece dizer mais sobre o meu critério do que sobre o agente —
acho que a régua que eu montei está severa demais.

**Não mexi na régua depois de ver o resultado.** Ficaria fácil ajustar até o número
melhorar, mas aí a métrica não estaria medindo nada além do meu próprio ajuste. Prefiro
reportar 0% e explicar o que ele significa.

### 6.3 Olhando cada divergência

**`CLI-029` — regra diz alto, agente diz baixo. Acho que o agente está certo.**

A Regra 1 pegou o dia 2026-05-26: 4 operações, R\$ 71.297,68, nenhuma chegando a
R\$ 20.000. Mas quando o agente foi olhar o dia e o histórico, achou um **saque**, dois
**depósitos** e uma **transferência recebida**, pra **quatro contrapartes diferentes**, numa
conta que tem 15 contrapartes distintas em 16 operações e gira R\$ 191 mil.

Isso não parece fracionamento. Fracionamento é pegar um valor e dividir em pedaços na mesma
direção, geralmente pra destinos ligados entre si. Aqui tem dinheiro entrando e saindo no
mesmo dia, espalhado. Parece só um dia movimentado de uma conta ativa.

Isso deixa três falhas da Regra 1 bem visíveis: ela não olha a direção do dinheiro, não
olha se as contrapartes têm relação, e não olha o tamanho do cliente. Três operações de
R\$ 18 mil são normais pra quem movimenta R\$ 191 mil e são muito estranhas pra quem
movimenta R\$ 20 mil — e a regra trata os dois igual.

**`CLI-014` — regra diz alto (3 sinalizações), agente diz baixo.**

A mediana desse cliente é R\$ 2.308,41, então o limite da Regra 2 cai em R\$ 11.542,05.
Três operações passaram disso. Só que em 11 operações somando R\$ 80.630, três valores
entre R\$ 13 mil e R\$ 23 mil não são três anomalias — é a parte de cima de uma distribuição
torta, que é normal.

O problema é que a Regra 2 conta cada ocorrência como se fosse independente, quando na
verdade é o mesmo fenômeno acontecendo três vezes. E meu jeito de montar o ranking piora
isso, porque soma as ocorrências — o `CLI-014` está em primeiro lugar no top 10 por causa
desse efeito, não porque seja o caso mais grave.

**`CLI-001` e `CLI-005` — regra diz alto, agente diz médio.**

Os dois casos adjacentes. O agente viu coisa que merece atenção mas não achou nada
conclusivo. Essa é a fronteira onde acho que dois analistas humanos também discordariam, e é
por isso que a concordância adjacente me pareceu mais útil de olhar do que a exata.

---

## 7. Onde isso quebra com dados reais

**Câmbio fixo.** A taxa 5,4 vale pra série inteira. Converter uma operação de março com
taxa de maio distorce, e pode criar ou apagar sinalização. Numa base real o câmbio é série
temporal e a conversão teria que ser na data da operação.

**A Regra 1 usa dia de calendário.** Quem fracionar entre 23h e 1h escapa fácil. Precisaria
de janela móvel.

**A Regra 1 ignora porte, direção e relação entre contrapartes.** Ver 6.3.

**A Regra 2 é frágil com poucas operações.** Com 4 pontos a mediana não descreve nada. E
com mediana baixa, qualquer valor médio já estoura 5×.

**Identificar contraparte por nome não funciona num cadastro real.** Sem CNPJ não dá pra
detectar operação entre partes relacionadas, que é uma das tipologias mais importantes de
PLD e que esse projeto simplesmente não enxerga.

**A LLM não é determinística.** Rodei o mesmo cliente duas vezes e vieram justificativas
diferentes, sendo que uma delas tinha aquele erro de inverter o limiar (5.2). Em coisa
regulatória isso pediria `temperature=0`, prompt versionado e trilha de auditoria — eu
registro a trajetória, mas não versiono o prompt.

**Não tenho gabarito.** Não sei se os alertas são verdadeiros, só sei se batem com a regra.
Toda a análise de divergência é argumento meu, não verificação.

**O agente concluiu 6 de 10.** Duas falhas de validação e duas de quota. Em produção
precisaria de fila com reprocessamento automático.

**Fiquei preso ao formato de erro do Groq** na recuperação da 5.5.

---

## 8. O que eu faria com mais tempo

### 8.1 Tapar o buraco que faz o modelo calcular

Acho que essa é a mais importante da lista, porque ataca a causa e não o sintoma.

O modelo calcula a razão `valor / mediana` porque precisa dela e ela não está no que eu
mando. Colocaria no `dossie_cliente()` e no retorno das ferramentas os valores **já
comparados**: `razao_sobre_mediana`, `limite_valor_atipico_brl`, `todas_abaixo_do_teto_20k`.

**Como eu testaria se funcionou:** rodava os 10 clientes antes e depois, e contava quantos
pareceres citam número que não está no insumo. Minha aposta é que cai pra zero. Se não
cair, minha explicação estava errada e o problema é outro.

### 8.2 Regras que olham o perfil do cliente

Trocar limiar fixo por limiar relativo ao histórico do próprio cliente. Fracionamento
passaria a exigir desvio do padrão dele, não só soma acima de R\$ 50 mil. E acrescentaria
as três coisas que faltam: direção do dinheiro, relação entre contrapartes e janela móvel
de 3 dias.

**Como testaria:** a regra nova teria que continuar pegando o `CLI-A-1` (fracionamento
claro) e parar de pegar o `CLI-029` (o falso positivo que o agente achou). Como não tenho
gabarito, esses dois casos serviriam como teste de regressão.

### 8.3 Não contar a mesma coisa três vezes na Regra 2

Contar *clientes com cauda estranha* em vez de *operações acima do limite*, ou exigir uma
dispersão mínima. Isso arrumaria o problema que colocou o `CLI-014` em primeiro no ranking.

### 8.4 Nível 3 — Trilha A (multiagente)

**Não fiz.** O que eu faria: encadear Triador (decide se o caso segue), Investigador (usa
as ferramentas do Nível 2) e Redator (escreve o parecer), com o estado compartilhado num
dataclass e a condição de parada no Triador — caso arquivado não passa adiante.

Escolheria a A por dois motivos. Primeiro porque reaproveitaria o `tools.py` como está.
Segundo porque o Triador resolveria um problema de custo que eu medi: hoje todo cliente
recebe investigação completa, inclusive os que o próprio agente resolve como risco baixo
depois de duas ferramentas.

**Como testaria:** comparando custo total e concordância contra o agente que já tenho. Se o
multiagente gastar mais tokens pra chegar nos mesmos pareceres, a arquitetura não se paga —
e eu diria isso em vez de esconder.

### 8.5 Deixar mais robusto

Fila com backoff e retomada, orçamento de tokens por execução, `temperature=0` com prompt
versionado, e salvar em `outputs/` com data e hora em vez de sobrescrever.

---

## 9. Onde eu mudei de ideia

Deixo isso registrado porque acho que mostra melhor como foi o trabalho do que fingir que
saiu tudo certo de primeira:

| O que eu tinha feito | O que rodar mostrou | Como ficou |
|---|---|---|
| `llama-3.3-70b` (do enunciado) | 404, modelo saiu do catálogo | `gpt-oss-120b`, depois `20b` |
| `max_tokens=1200` | 79% dos tokens de saída eram raciocínio invisível | 3000, com base em medição |
| `red_flags` com `min_length=1` | não deixava dizer "não achei nada" | exigência só quando o risco é alto |
| Retry pra corrigir o formato | falhou 2 de 2 com o mesmo erro | normalização no schema |
| Conclusão em texto livre | modelo inventava ferramenta pra entregar | `emitir_parecer` como ferramenta |
| Lote sobrescrevendo sempre | apagou 5 pareceres bons | mantém o melhor status |
| Critério do enunciado no confronto | conjunto vazio | critério graduado meu |
